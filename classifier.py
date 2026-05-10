#!/usr/bin/env python3
"""AI File Classifier — local AI-powered photo and video organiser."""

import argparse
import sys
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

import config
from src.database import Database
from src.scanner import Scanner
from src.analyzers.image_analyzer import ImageAnalyzer
from src.analyzers.video_analyzer import VideoAnalyzer
from src.analyzers.duplicate_detector import DuplicateDetector
from src.organizer import Organizer
from src.reporter import Reporter

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Local AI photo & video classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python classifier.py --input "C:\\Photos" --dry-run
  python classifier.py --input "C:\\Photos" --move
  python classifier.py --input "C:\\Media" --output "D:\\Review" --move --model qwen2.5-vl:7b
""",
    )
    p.add_argument("--input",  "-i", required=True,              help="Folder to scan")
    p.add_argument("--output", "-o", default=None,               help="Review folder (default: <input>/AI Review)")
    p.add_argument("--model",  "-m", default=config.DEFAULT_MODEL, help=f"Ollama model (default: {config.DEFAULT_MODEL})")
    p.add_argument("--move",   action="store_true",              help="Move files (default: dry run)")
    p.add_argument("--no-video",  action="store_true",           help="Skip video analysis")
    p.add_argument("--no-audio",  action="store_true",           help="Skip audio transcription")
    p.add_argument("--db",     default="./classifier.db",        help="SQLite DB path")
    p.add_argument("--report", default="./report.html",          help="HTML report output path")
    p.add_argument("--ollama-host", default=config.OLLAMA_HOST,  help="Ollama base URL")
    p.add_argument("--blur-threshold", type=float, default=config.BLUR_THRESHOLD, help="Blur threshold")
    p.add_argument("--reset",  action="store_true",              help="Wipe DB and start fresh")
    return p.parse_args()


def check_dependencies(args: argparse.Namespace) -> tuple[bool, bool]:
    """Returns (ollama_ok, ffmpeg_ok)."""
    # Ollama
    try:
        resp = requests.get(f"{args.ollama_host}/api/tags", timeout=5)
        models = [m['name'] for m in resp.json().get('models', [])]
        model_base = args.model.split(':')[0]
        if not any(model_base in m for m in models):
            console.print(f"[yellow]⚠  Model '{args.model}' not found locally.[/yellow]")
            console.print(f"[cyan]   Pull it with:  ollama pull {args.model}[/cyan]")
            console.print("[yellow]   Continuing — Ollama will auto-download on first use.[/yellow]")
    except Exception:
        console.print("[red]✗  Ollama is not running.[/red]")
        console.print("[cyan]   Start it with:  ollama serve[/cyan]")
        return False, False

    # FFmpeg
    try:
        import subprocess
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        ffmpeg_ok = True
    except Exception:
        ffmpeg_ok = False
        if not args.no_video:
            console.print("[yellow]⚠  FFmpeg not found — video analysis disabled.[/yellow]")
            console.print("[yellow]   Install from: https://ffmpeg.org/download.html[/yellow]")

    return True, ffmpeg_ok


def main():
    args = parse_args()

    console.print(Panel.fit(
        "[bold cyan]AI File Classifier[/bold cyan]\n"
        "[dim]Local AI-powered photo & video organiser[/dim]",
        border_style="cyan",
    ))

    input_path = Path(args.input)
    if not input_path.exists():
        console.print(f"[red]✗ Input folder not found: {args.input}[/red]")
        sys.exit(1)

    # Default output sits inside the input folder
    output_path = Path(args.output) if args.output else input_path / "AI Review"
    args.output = str(output_path)

    ollama_ok, ffmpeg_ok = check_dependencies(args)
    if not ollama_ok:
        sys.exit(1)

    skip_video = args.no_video or not ffmpeg_ok

    # Initialise components
    db = Database(args.db)
    if args.reset:
        db.reset()
        console.print("[yellow]Database reset.[/yellow]")

    scanner    = Scanner()
    img_anal   = ImageAnalyzer(args.ollama_host, args.model, args.blur_threshold)
    vid_anal   = None if skip_video else VideoAnalyzer(args.ollama_host, args.model, not args.no_audio)
    dup_det    = DuplicateDetector(db)
    organizer  = Organizer(args.output, dry_run=not args.move)
    reporter   = Reporter(db)

    # Scan
    console.print(f"\n[bold]Scanning:[/bold] {input_path}")
    files = scanner.scan(input_path, skip_video=skip_video)
    images = [f for f in files if scanner.is_image(f)]
    videos = [f for f in files if scanner.is_video(f)]
    console.print(f"Found [cyan]{len(images)}[/cyan] image(s), [cyan]{len(videos)}[/cyan] video(s)\n")

    # Analyse
    processed = 0
    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analysing...", total=len(files))

        for file_path in files:
            progress.update(task, description=f"[dim]{file_path.name[:55]}[/dim]")

            if db.is_analyzed(str(file_path)):
                progress.advance(task)
                continue

            try:
                if scanner.is_image(file_path):
                    result = img_anal.analyze(file_path)
                else:
                    result = vid_anal.analyze(file_path)
                db.save_result(file_path, result)
                new_path = organizer.organize_one(db.get_result(str(file_path)))
                if new_path:
                    db.update_moved_to(str(file_path), new_path)
                processed += 1
                if processed % 25 == 0:
                    reporter.generate(args.report)
            except Exception as e:
                console.print(f"[red]  Error ({file_path.name}): {e}[/red]")

            progress.advance(task)

    # Duplicate detection
    console.print("\n[bold]Detecting duplicates…[/bold]")
    groups = dup_det.find_duplicates()
    console.print(f"Found [cyan]{len(groups)}[/cyan] duplicate group(s)\n")

    # Organise
    if args.move:
        console.print(f"[bold]Moving files → {args.output}[/bold]\n")
    else:
        console.print("[bold yellow]DRY RUN[/bold yellow] (no files moved)\n")
    organizer.organize(db.get_all_results())

    # Report
    reporter.generate(args.report)
    console.print(f"\n[green]✓ Report:[/green] {args.report}")

    # Summary panel
    s = db.get_stats()
    console.print(Panel(
        f"[bold]Results[/bold]\n"
        f"Total: {s['total']}\n"
        f"[green]Keep: {s['keep']}[/green]  "
        f"[yellow]Review: {s['review']}[/yellow]  "
        f"[red]Probably delete: {s['probably_delete']}[/red]\n"
        f"Duplicates: {s['duplicates']}  Low quality: {s['low_quality']}",
        border_style="green",
    ))


if __name__ == "__main__":
    main()

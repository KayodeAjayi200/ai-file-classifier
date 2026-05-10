OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-vl:4b"

BLUR_THRESHOLD = 100.0          # Laplacian variance below this = blurry
DUPLICATE_THRESHOLD = 10        # Perceptual hash Hamming distance below this = duplicate

VIDEO_FRAME_INTERVAL = 30       # Extract one frame every N seconds
MAX_FRAMES_PER_VIDEO = 8        # Cap on frames per video

REQUEST_TIMEOUT = 120           # Ollama request timeout (seconds)

SUPPORTED_IMAGES = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp',
    '.webp', '.tiff', '.tif', '.heic', '.heif', '.avif',
}
SUPPORTED_VIDEOS = {
    '.mp4', '.mov', '.avi', '.mkv', '.wmv',
    '.m4v', '.3gp', '.ts', '.mts', '.mxf', '.flv', '.webm',
}

# Maps AI category → output subfolder name
CATEGORY_FOLDERS = {
    'selfie':          'Memories',
    'group_photo':     'Memories',
    'family':          'Memories',
    'friends':         'Memories',
    'celebration':     'Memories',
    'travel':          'Memories',
    'food':            'Personal',
    'pets':            'Personal',
    'outdoors':        'Personal',
    'home':            'Personal',
    'other':           'Personal',
    'screenshot':      'Screenshots',
    'screen_recording':'Screenshots',
    'document':        'Documents',
    'receipt':         'Documents',
    'meme':            'Junk',
    'whatsapp_junk':   'Junk',
    'social_save':     'Junk',
    'wallpaper':       'Junk',
    'junk':            'Junk',
}

# Action/quality override folders (checked before category)
REVIEW_FOLDERS = {
    'duplicates':      'Duplicates',
    'low_quality':     'Low Quality',
    'probably_delete': 'Probably Delete',
    'review':          'Review',
}

EMBEDDER_CONFIGS = {
    "default": {
        "splitter": "recursive",
        "chunk_size": 512,
        "chunk_overlap": 50,
        "batch_size": 5,
        "max_retries": 3,
    },
    "character_test": {
        "splitter": "character",
        "chunk_size": 300,
        "chunk_overlap": 30,
        "batch_size": 10,
        "max_retries": 3,
    },
    "token_test": {
        "splitter": "token",
        "chunk_size": 400,
        "chunk_overlap": 40,
        "batch_size": 8,
        "max_retries": 5,
    },
}

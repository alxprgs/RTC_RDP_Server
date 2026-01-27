import os
import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_ = os.getenv("RELOAD", "0") == "1"

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=reload_,
    )

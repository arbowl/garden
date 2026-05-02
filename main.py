import logging

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8003, reload=True)

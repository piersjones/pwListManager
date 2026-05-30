#!/usr/bin/env python3
import argparse
import logging
import os
from src.web.app import create_app
from src.logger import setup_logger

class SuppressSSLRequests(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if '\x16\x03\x01' in msg or 'Bad request version' in msg:
            return False
        return True

def main():
    parser = argparse.ArgumentParser(description="pwListManager Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5050, help="Port (default: 5050)", )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    level_name = "DEBUG" if args.debug else "INFO"
    logger = setup_logger(level_name)
    logger.info("pwListManager web server starting on %s:%d (log level=%s, threaded=True)", args.host, args.port, level_name)

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.addFilter(SuppressSSLRequests())

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)

if __name__ == "__main__":
    main()
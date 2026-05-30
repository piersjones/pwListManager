import sys
from src.config import Config, ConfigError
from src.logger import setup_logger
from src.trakt_client import TraktClient, TraktClientError

def main():
    logger = setup_logger("INFO")
    logger.info("Initializing pwListManager Trakt Migration CLI...")
    
    try:
        config = Config()
    except ConfigError as ce:
        logger.critical(f"Configuration error: {ce}")
        sys.exit(1)
        
    try:
        # Load Trakt Client
        client = TraktClient(config, logger)
        logger.info("Checking authentication...")
        client.authenticate()
        
        # Get default watchlist
        logger.info("Fetching default watchlist...")
        watchlist = client.get_watchlist_movies()
        logger.info(f"Found {len(watchlist)} movies in default watchlist.")
        
        if not watchlist:
            logger.info("Default watchlist is empty. Nothing to migrate.")
            return

        # Print films to be migrated
        for item in watchlist:
            movie = item["movie"]
            logger.info(f" - {movie['title']} ({movie['year']}) [Trakt ID: {movie['ids']['trakt']}]")
            
        # Get or create custom list ID
        custom_list_slug = client.get_custom_list_id()
        logger.info(f"Target custom list: '{custom_list_slug}'")
        
        # Add to custom list
        logger.info("Adding movies to custom list...")
        client.add_to_custom_list(custom_list_slug, watchlist)
        
        # Remove from default watchlist
        logger.info("Removing movies from default watchlist...")
        client.remove_from_watchlist(watchlist)
        
        logger.info("Migration test completed successfully!")
        
    except TraktClientError as tce:
        logger.error(f"Trakt API error: {tce}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

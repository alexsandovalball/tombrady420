"""
Cross-bot message routing for multi-bot deployments.
Routes admin messages to users across different bot instances.
"""
import logging
from typing import Dict, List, Any
from telegram.ext import Application
from telegram.error import TelegramError, Forbidden

logger = logging.getLogger(__name__)


class CrossBotMessageRouter:
    """Routes admin messages to users across different bot instances."""
    
    def __init__(self, db):
        """
        Initialize the message router.
        
        Args:
            db: Database instance for user lookups
        """
        self.db = db
    
    async def broadcast_message(
        self,
        message_text: str,
        admin_id: int,
        bot_applications: Dict[int, Application],
        exclude_blocked: bool = True
    ) -> Dict[str, int]:
        """
        Broadcast message to all users across all bot instances.
        
        Args:
            message_text: Message to broadcast
            admin_id: ID of admin sending the message
            bot_applications: Dict mapping bot_instance -> Application
            exclude_blocked: Whether to exclude blocked users
            
        Returns:
            Dictionary with broadcast statistics:
            {
                'total_users': 1000,
                'bot_1': 500,
                'bot_2': 300,
                'bot_3': 200,
                'queued': 1000
            }
        """
        try:
            # Get all users grouped by bot instance
            users_by_bot = await self.db.get_users_by_bot_instance()
            
            total_users = 0
            stats = {}
            
            # Queue messages for each bot instance
            for bot_instance, users in users_by_bot.items():
                # Filter out blocked users and admins
                eligible_users = []
                for user in users:
                    user_id = user['user_id']
                    
                    # Skip blocked users
                    if exclude_blocked and user.get('is_blocked', 0) == 1:
                        continue
                    
                    # Skip admin users
                    from utils.helpers import get_admin_ids
                    if user_id in get_admin_ids():
                        continue
                    
                    eligible_users.append(user_id)
                
                # Queue custom messages for this bot instance
                for user_id in eligible_users:
                    await self.db.queue_custom_message(user_id, message_text, bot_instance)
                
                stats[f'bot_{bot_instance}'] = len(eligible_users)
                total_users += len(eligible_users)
            
            stats['total_users'] = total_users
            stats['queued'] = total_users
            stats['bot_instances'] = len(users_by_bot)
            
            logger.info(f"Broadcast queued for {total_users} users across {len(users_by_bot)} bot instances")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error broadcasting message across bots: {e}", exc_info=True)
            return {
                'total_users': 0,
                'queued': 0,
                'error': str(e)
            }
    
    async def send_to_user(
        self,
        user_id: int,
        message_text: str,
        bot_applications: Dict[int, Application]
    ) -> bool:
        """
        Send message to specific user via their registered bot instance.
        
        Args:
            user_id: Target user ID
            message_text: Message to send
            bot_applications: Dict mapping bot_instance -> Application
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        try:
            # Get user's bot instance
            bot_instance = await self.db.get_user_bot_instance(user_id)
            
            if bot_instance is None:
                logger.warning(f"User {user_id} not found in database")
                return False
            
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                logger.warning(f"Cannot send message to blocked user {user_id}")
                return False
            
            # Get the appropriate bot application
            if bot_instance not in bot_applications:
                logger.error(f"Bot instance {bot_instance} not found in applications")
                return False
            
            app = bot_applications[bot_instance]
            
            # Send message
            await app.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode="Markdown"
            )
            
            logger.info(f"Sent message to user {user_id} via bot instance {bot_instance}")
            return True
            
        except Forbidden:
            logger.info(f"User {user_id} blocked the bot, marking as blocked")
            await self.db.block_user(user_id)
            return False
            
        except TelegramError as e:
            logger.error(f"Telegram error sending to user {user_id}: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Error sending message to user {user_id}: {e}", exc_info=True)
            return False
    
    async def get_user_distribution(self) -> Dict[int, int]:
        """
        Get distribution of users across bot instances.
        
        Returns:
            Dictionary mapping bot_instance -> user_count
        """
        try:
            users_by_bot = await self.db.get_users_by_bot_instance()
            return {
                bot_instance: len(users) 
                for bot_instance, users in users_by_bot.items()
            }
        except Exception as e:
            logger.error(f"Error getting user distribution: {e}")
            return {}

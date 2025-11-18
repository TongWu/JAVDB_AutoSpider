"""
Proxy Ban Manager

Manages proxy ban records with persistent storage.
Records when proxies are banned and tracks their expected unban time (7 days).
"""

import os
import csv
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from threading import Lock


logger = logging.getLogger(__name__)


class ProxyBanRecord:
    """Record of a proxy ban"""
    
    def __init__(self, proxy_name: str, ban_time: datetime, unban_time: datetime, 
                 proxy_url: Optional[str] = None):
        self.proxy_name = proxy_name
        self.ban_time = ban_time
        self.unban_time = unban_time
        self.proxy_url = proxy_url  # Full URL with IP (for email, not logged to file)
        
    def is_still_banned(self) -> bool:
        """Check if proxy is still in ban period"""
        return datetime.now() < self.unban_time
    
    def time_until_unban(self) -> timedelta:
        """Get remaining time until unban"""
        return self.unban_time - datetime.now()
    
    def days_until_unban(self) -> int:
        """Get remaining days until unban"""
        delta = self.time_until_unban()
        return max(0, delta.days)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary (for CSV/logging - without IP)"""
        return {
            'proxy_name': self.proxy_name,
            'ban_time': self.ban_time.strftime('%Y-%m-%d %H:%M:%S'),
            'unban_time': self.unban_time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    
    def to_dict_with_ip(self) -> Dict:
        """Convert to dictionary with IP info (for email only)"""
        data = self.to_dict()
        data['proxy_url'] = self.proxy_url if self.proxy_url else 'N/A'
        return data


class ProxyBanManager:
    """Manages proxy ban records with persistent storage"""
    
    BAN_DURATION_DAYS = 7  # JavDB bans IPs for 7 days
    COOLDOWN_DURATION_DAYS = 8  # 8 days cooldown (7 days ban + 1 day buffer)
    
    def __init__(self, ban_log_file: str = 'logs/proxy_bans.csv'):
        self.ban_log_file = ban_log_file
        self.banned_proxies: Dict[str, ProxyBanRecord] = {}
        self.lock = Lock()
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(ban_log_file), exist_ok=True)
        
        # Load existing ban records
        self._load_ban_records()
        
        # Clean up expired bans
        self._cleanup_expired_bans()
    
    def _load_ban_records(self):
        """Load ban records from CSV file"""
        if not os.path.exists(self.ban_log_file):
            logger.info(f"No existing ban log found at {self.ban_log_file}")
            return
        
        try:
            with open(self.ban_log_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    proxy_name = row['proxy_name']
                    ban_time = datetime.strptime(row['ban_time'], '%Y-%m-%d %H:%M:%S')
                    unban_time = datetime.strptime(row['unban_time'], '%Y-%m-%d %H:%M:%S')
                    
                    record = ProxyBanRecord(proxy_name, ban_time, unban_time)
                    self.banned_proxies[proxy_name] = record
            
            logger.info(f"Loaded {len(self.banned_proxies)} ban records from {self.ban_log_file}")
        except Exception as e:
            logger.error(f"Error loading ban records: {e}")
    
    def _save_ban_records(self):
        """Save ban records to CSV file (without IP information)"""
        try:
            with open(self.ban_log_file, 'w', newline='', encoding='utf-8') as f:
                fieldnames = ['proxy_name', 'ban_time', 'unban_time']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for record in self.banned_proxies.values():
                    writer.writerow(record.to_dict())
            
            logger.debug(f"Saved {len(self.banned_proxies)} ban records to {self.ban_log_file}")
        except Exception as e:
            logger.error(f"Error saving ban records: {e}")
    
    def _cleanup_expired_bans(self):
        """Remove expired ban records"""
        with self.lock:
            expired = [name for name, record in self.banned_proxies.items() 
                      if not record.is_still_banned()]
            
            for proxy_name in expired:
                del self.banned_proxies[proxy_name]
                logger.info(f"Removed expired ban record for proxy '{proxy_name}'")
            
            if expired:
                self._save_ban_records()
    
    def is_proxy_banned(self, proxy_name: str) -> bool:
        """Check if a proxy is currently banned"""
        with self.lock:
            if proxy_name not in self.banned_proxies:
                return False
            
            record = self.banned_proxies[proxy_name]
            if not record.is_still_banned():
                # Ban expired, remove it
                del self.banned_proxies[proxy_name]
                self._save_ban_records()
                return False
            
            return True
    
    def add_ban(self, proxy_name: str, proxy_url: Optional[str] = None):
        """
        Add a new ban record for a proxy
        
        Args:
            proxy_name: Name of the proxy
            proxy_url: Full proxy URL with IP (for email reporting)
        """
        with self.lock:
            # Check if already banned
            if proxy_name in self.banned_proxies:
                existing = self.banned_proxies[proxy_name]
                if existing.is_still_banned():
                    logger.warning(f"Proxy '{proxy_name}' is already in ban period, not updating")
                    return
            
            # Create new ban record
            ban_time = datetime.now()
            unban_time = ban_time + timedelta(days=self.BAN_DURATION_DAYS)
            
            record = ProxyBanRecord(proxy_name, ban_time, unban_time, proxy_url)
            self.banned_proxies[proxy_name] = record
            
            logger.warning(
                f"Proxy '{proxy_name}' banned until {unban_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"({self.BAN_DURATION_DAYS} days)"
            )
            
            # Save to file
            self._save_ban_records()
    
    def get_banned_proxies(self) -> List[ProxyBanRecord]:
        """Get list of currently banned proxies"""
        with self.lock:
            self._cleanup_expired_bans()
            return list(self.banned_proxies.values())
    
    def get_ban_summary(self, include_ip: bool = False) -> str:
        """
        Get a formatted summary of banned proxies
        
        Args:
            include_ip: Whether to include IP information (for email)
        
        Returns:
            Formatted string summary
        """
        banned = self.get_banned_proxies()
        
        if not banned:
            return "No proxies currently banned."
        
        lines = [f"Currently banned proxies: {len(banned)}"]
        lines.append("")
        
        for record in sorted(banned, key=lambda r: r.unban_time):
            days_left = record.days_until_unban()
            hours_left = int(record.time_until_unban().total_seconds() / 3600) % 24
            
            line = f"  - {record.proxy_name}:"
            if include_ip and record.proxy_url:
                line += f"\n    IP: {record.proxy_url}"
            line += f"\n    Banned at: {record.ban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            line += f"\n    Will unban: {record.unban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            line += f"\n    Time remaining: {days_left} days {hours_left} hours"
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def get_cooldown_seconds(self) -> int:
        """Get cooldown duration in seconds (8 days)"""
        return self.COOLDOWN_DURATION_DAYS * 24 * 3600


# Global ban manager instance
_global_ban_manager: Optional[ProxyBanManager] = None


def get_ban_manager(ban_log_file: str = 'logs/proxy_bans.csv') -> ProxyBanManager:
    """Get or create the global ban manager instance"""
    global _global_ban_manager
    
    if _global_ban_manager is None:
        _global_ban_manager = ProxyBanManager(ban_log_file)
    
    return _global_ban_manager


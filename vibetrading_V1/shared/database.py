"""
Database Connection Management
PostgreSQL (async) and QuestDB connections.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from shared.config import get_settings

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLAlchemy Base
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PostgreSQL Connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PostgresDatabase:
    """PostgreSQL async database connection manager."""
    
    _instance: Optional["PostgresDatabase"] = None
    
    def __init__(self) -> None:
        settings = get_settings()
        self.engine = create_async_engine(
            settings.database.url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    
    @classmethod
    def get_instance(cls) -> "PostgresDatabase":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get database session context manager."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    
    async def health_check(self) -> bool:
        """Check database connectivity."""
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False
    
    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()
        PostgresDatabase._instance = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# QuestDB Connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import socket
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Dict

import requests


class QuestDBDatabase:
    """QuestDB connection manager using ILP and HTTP API."""
    
    _instance: Optional["QuestDBDatabase"] = None
    
    def __init__(self) -> None:
        settings = get_settings()
        self.http_url = settings.questdb.http_url
        self.ilp_host = settings.questdb.host
        self.ilp_port = settings.questdb.ilp_port
        self._ilp_socket: Optional[socket.socket] = None
    
    @classmethod
    def get_instance(cls) -> "QuestDBDatabase":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _get_ilp_socket(self) -> socket.socket:
        """Get or create ILP socket connection."""
        if self._ilp_socket is None:
            self._ilp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._ilp_socket.connect((self.ilp_host, self.ilp_port))
        return self._ilp_socket
    
    def write_line(self, table: str, tags: Dict[str, str], fields: Dict[str, Any], 
                   timestamp_ns: Optional[int] = None) -> None:
        """
        Write a single line using InfluxDB Line Protocol.
        
        Args:
            table: Table name
            tags: Tag key-value pairs (indexed)
            fields: Field key-value pairs
            timestamp_ns: Timestamp in nanoseconds (optional)
        """
        # Build line protocol string
        # Format: table,tag1=val1,tag2=val2 field1=val1,field2=val2 timestamp
        
        tag_str = ",".join(f"{k}={v}" for k, v in tags.items())
        
        field_parts = []
        for k, v in fields.items():
            if isinstance(v, str):
                field_parts.append(f'{k}="{v}"')
            elif isinstance(v, bool):
                field_parts.append(f"{k}={'t' if v else 'f'}")
            elif isinstance(v, (int, float, Decimal)):
                field_parts.append(f"{k}={v}")
            elif v is None:
                continue
            else:
                field_parts.append(f'{k}="{str(v)}"')
        
        field_str = ",".join(field_parts)
        
        if tag_str:
            line = f"{table},{tag_str} {field_str}"
        else:
            line = f"{table} {field_str}"
        
        if timestamp_ns:
            line += f" {timestamp_ns}"
        
        line += "\n"
        
        sock = self._get_ilp_socket()
        sock.sendall(line.encode())
    
    def query(self, sql: str, timeout: int = 30) -> List[Dict[str, Any]]:
        """
        Execute SQL query via HTTP API.
        
        Args:
            sql: SQL query string
            timeout: Request timeout in seconds
            
        Returns:
            List of row dictionaries
        """
        url = f"{self.http_url}/exec"
        try:
            response = requests.get(url, params={"query": sql}, timeout=timeout)
            response.raise_for_status()
            
            data = response.json()
            if "dataset" not in data:
                return []
            
            columns = data.get("columns", [])
            column_names = [col["name"] for col in columns]
            
            results = []
            for row in data["dataset"]:
                results.append(dict(zip(column_names, row)))
            
            return results
            
        except requests.RequestException as e:
            logger.error(f"QuestDB query failed: {e}")
            raise
    
    def health_check(self) -> bool:
        """Check QuestDB connectivity."""
        try:
            response = requests.get(f"{self.http_url}/", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False
    
    def close(self) -> None:
        """Close connections."""
        if self._ilp_socket:
            try:
                self._ilp_socket.close()
            except Exception:
                pass
            self._ilp_socket = None
        QuestDBDatabase._instance = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Convenience Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_postgres() -> PostgresDatabase:
    """Get PostgreSQL database instance."""
    return PostgresDatabase.get_instance()


def get_questdb() -> QuestDBDatabase:
    """Get QuestDB database instance."""
    return QuestDBDatabase.get_instance()


async def check_all_databases() -> Dict[str, bool]:
    """Check health of all database connections."""
    postgres = get_postgres()
    questdb = get_questdb()
    
    return {
        "postgres": await postgres.health_check(),
        "questdb": questdb.health_check(),
    }

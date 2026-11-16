"""Persist only the six operator-editable scheduling settings."""
import json

import aiosqlite


async def initialize_settings(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS runtime_settings (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            values_json TEXT NOT NULL
        )
    """)


class SettingsStoreMixin:
    async def get_runtime_settings(self):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT values_json FROM runtime_settings WHERE singleton=1")
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else {}

    async def save_runtime_settings(self, values):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO runtime_settings(singleton, values_json) VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET values_json=excluded.values_json
            """, (json.dumps(values),))
            await db.commit()

#!/usr/bin/env python3
"""
Diagnostic script for inspecting notification system state in production.

This script:
- Detects the database path from environment or default location
- Queries notification_queue and custom_message_queue tables
- Prints counts and sample rows for debugging
- Writes diagnostics output to diagnostics_output.json

Usage:
    python diagnostics/diagnose_notifications.py

In Render service shell:
    cd /opt/render/project/src
    python diagnostics/diagnose_notifications.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def detect_db_path():
    """Detect the database path from environment or default location."""
    # Try environment variable first
    db_path = os.getenv('DATABASE_PATH')
    if db_path and os.path.exists(db_path):
        return db_path
    
    # Try common locations
    common_paths = [
        'bot_data.db',
        '/opt/render/project/src/bot_data.db',
        os.path.join(os.path.dirname(__file__), '..', 'bot_data.db'),
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            return path
    
    # Default fallback
    return 'bot_data.db'


def get_queue_diagnostics(db_path):
    """Get diagnostic information from notification queues."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        diagnostics = {
            'timestamp': datetime.utcnow().isoformat(),
            'db_path': db_path,
            'db_exists': os.path.exists(db_path),
            'db_size_bytes': os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        }
        
        # Check if tables exist
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN ('notification_queue', 'custom_message_queue')
        """)
        existing_tables = [row[0] for row in cursor.fetchall()]
        diagnostics['tables_exist'] = existing_tables
        
        if 'notification_queue' in existing_tables:
            # Get notification_queue statistics
            cursor.execute("SELECT COUNT(*) as total FROM notification_queue")
            total = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) as pending FROM notification_queue WHERE sent_at IS NULL")
            pending = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) as sent FROM notification_queue WHERE sent_at IS NOT NULL")
            sent = cursor.fetchone()[0]
            
            diagnostics['notification_queue'] = {
                'total_count': total,
                'pending_count': pending,
                'sent_count': sent,
            }
            
            # Get sample pending notifications
            cursor.execute("""
                SELECT id, user_id, product_id, created_at, sent_at
                FROM notification_queue
                WHERE sent_at IS NULL
                ORDER BY created_at DESC
                LIMIT 10
            """)
            samples = []
            for row in cursor.fetchall():
                samples.append({
                    'id': row[0],
                    'user_id': row[1],
                    'product_id': row[2],
                    'created_at': row[3],
                    'sent_at': row[4],
                })
            diagnostics['notification_queue']['sample_pending'] = samples
            
        else:
            diagnostics['notification_queue'] = {'error': 'table does not exist'}
        
        if 'custom_message_queue' in existing_tables:
            # Get custom_message_queue statistics
            cursor.execute("SELECT COUNT(*) as total FROM custom_message_queue")
            total = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) as pending FROM custom_message_queue WHERE sent_at IS NULL")
            pending = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) as sent FROM custom_message_queue WHERE sent_at IS NOT NULL")
            sent = cursor.fetchone()[0]
            
            diagnostics['custom_message_queue'] = {
                'total_count': total,
                'pending_count': pending,
                'sent_count': sent,
            }
            
            # Get sample pending custom messages
            cursor.execute("""
                SELECT id, user_id, message_text, media_type, media_file_id, created_at, sent_at
                FROM custom_message_queue
                WHERE sent_at IS NULL
                ORDER BY created_at DESC
                LIMIT 10
            """)
            samples = []
            for row in cursor.fetchall():
                samples.append({
                    'id': row[0],
                    'user_id': row[1],
                    'message_text': row[2][:100] if row[2] else None,  # Truncate long messages
                    'media_type': row[3],
                    'media_file_id': row[4][:50] if row[4] else None,  # Truncate file IDs
                    'created_at': row[5],
                    'sent_at': row[6],
                })
            diagnostics['custom_message_queue']['sample_pending'] = samples
            
        else:
            diagnostics['custom_message_queue'] = {'error': 'table does not exist'}
        
        conn.close()
        return diagnostics
        
    except Exception as e:
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'db_path': db_path,
            'error': str(e),
            'error_type': type(e).__name__,
        }


def main():
    """Main diagnostic script."""
    print("=" * 70)
    print("NOTIFICATION SYSTEM DIAGNOSTICS")
    print("=" * 70)
    print()
    
    # Detect database path
    db_path = detect_db_path()
    print(f"Database path: {db_path}")
    print(f"Database exists: {os.path.exists(db_path)}")
    
    if os.path.exists(db_path):
        print(f"Database size: {os.path.getsize(db_path):,} bytes")
    print()
    
    # Get diagnostics
    print("Querying notification queues...")
    diagnostics = get_queue_diagnostics(db_path)
    
    # Print results
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print()
    
    if 'error' in diagnostics:
        print(f"❌ ERROR: {diagnostics['error']}")
        print(f"   Type: {diagnostics.get('error_type', 'Unknown')}")
    else:
        # Print notification_queue info
        if 'notification_queue' in diagnostics:
            nq = diagnostics['notification_queue']
            if 'error' in nq:
                print(f"notification_queue: ❌ {nq['error']}")
            else:
                print("notification_queue:")
                print(f"  Total: {nq['total_count']}")
                print(f"  Pending: {nq['pending_count']}")
                print(f"  Sent: {nq['sent_count']}")
                
                if nq.get('sample_pending'):
                    print(f"\n  Sample pending notifications ({len(nq['sample_pending'])} rows):")
                    for sample in nq['sample_pending'][:5]:  # Show first 5
                        print(f"    - ID {sample['id']}: user={sample['user_id']}, "
                              f"product={sample['product_id']}, created={sample['created_at']}")
        
        print()
        
        # Print custom_message_queue info
        if 'custom_message_queue' in diagnostics:
            cmq = diagnostics['custom_message_queue']
            if 'error' in cmq:
                print(f"custom_message_queue: ❌ {cmq['error']}")
            else:
                print("custom_message_queue:")
                print(f"  Total: {cmq['total_count']}")
                print(f"  Pending: {cmq['pending_count']}")
                print(f"  Sent: {cmq['sent_count']}")
                
                if cmq.get('sample_pending'):
                    print(f"\n  Sample pending messages ({len(cmq['sample_pending'])} rows):")
                    for sample in cmq['sample_pending'][:5]:  # Show first 5
                        msg_preview = sample['message_text'][:50] if sample['message_text'] else '(no text)'
                        print(f"    - ID {sample['id']}: user={sample['user_id']}, "
                              f"media={sample['media_type']}, msg=\"{msg_preview}...\"")
    
    # Write diagnostics output to JSON file
    output_file = 'diagnostics_output.json'
    try:
        with open(output_file, 'w') as f:
            json.dump(diagnostics, f, indent=2)
        print()
        print(f"✅ Diagnostics written to {output_file}")
    except Exception as e:
        print()
        print(f"❌ Failed to write diagnostics file: {e}")
    
    print()
    print("=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)
    
    return 0 if 'error' not in diagnostics else 1


if __name__ == '__main__':
    sys.exit(main())

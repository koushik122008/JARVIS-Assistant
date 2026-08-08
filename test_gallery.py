#!/usr/bin/env python3
"""
Test script for gallery functionality.
Tests the gallery API endpoint and verifies images are returned correctly.
"""

import requests
import json
from pathlib import Path


def test_gallery():
    """Test the gallery API endpoint."""
    base_url = "http://localhost:8000"
    
    # First, get a token by logging in
    print("[TEST] Getting authentication token...")
    
    # Create a new key
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "dashboard"))
        from server import DashboardServer
        server = DashboardServer()
        key = server.new_key()
        print(f"[INFO] Generated key: {key}")
    except Exception as e:
        print(f"[ERROR] Could not generate key: {e}")
        return
    
    # Login with the key
    try:
        login_response = requests.post(
            f"{base_url}/login",
            json={"pin": key},
            headers={"Content-Type": "application/json"}
        )
        if login_response.status_code == 200:
            token_data = login_response.json()
            if token_data.get("ok"):
                token = token_data["token"]
                print(f"[OK] Got token: {token[:20]}...")
            else:
                print(f"[ERROR] Login failed: {token_data}")
                return
        else:
            print(f"[ERROR] Login request failed with status: {login_response.status_code}")
            return
    except Exception as e:
        print(f"[ERROR] Login request failed: {e}")
        return
    
    # Test the gallery endpoint
    print("\n[TEST] Testing gallery endpoint...")
    try:
        gallery_response = requests.get(
            f"{base_url}/api/gallery",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if gallery_response.status_code == 200:
            gallery_data = gallery_response.json()
            images = gallery_data.get("images", [])
            count = gallery_data.get("count", 0)
            
            print(f"[OK] Gallery endpoint returned {count} images")
            
            if images:
                print("\n[IMAGES] Found images:")
                for img in images[:5]:  # Show first 5 images
                    print(f"  - {img['name']} ({img['size']} bytes)")
            else:
                print("[INFO] No images found in gallery")
                
        else:
            print(f"[ERROR] Gallery request failed with status: {gallery_response.status_code}")
            print(f"[ERROR] Response: {gallery_response.text}")
            
    except Exception as e:
        print(f"[ERROR] Gallery request failed: {e}")
    
    # Test the gallery image endpoint if there are images
    if images:
        print("\n[TEST] Testing gallery image endpoint...")
        first_image = images[0]
        try:
            image_response = requests.get(
                f"{base_url}/gallery/{first_image['name']}?token={token}",
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if image_response.status_code == 200:
                print(f"[OK] Image endpoint returned {len(image_response.content)} bytes")
            else:
                print(f"[ERROR] Image request failed with status: {image_response.status_code}")
                
        except Exception as e:
            print(f"[ERROR] Image request failed: {e}")


if __name__ == "__main__":
    print("[START] Testing gallery functionality...\n")
    test_gallery()
    print("\n[DONE] Gallery test completed")

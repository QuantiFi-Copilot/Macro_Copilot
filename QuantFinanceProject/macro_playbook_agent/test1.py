import requests
from pprint import pprint

def test_remote_api():
    print("Testing remote API endpoints...\n")

    endpoints = {
        "News": "https://mc-api-j0rn.onrender.com/api/news",
        "Latest News": "https://mc-api-j0rn.onrender.com/api/latest_news",
        "Business News": "https://mc-api-j0rn.onrender.com/api/business_news",
        "List": "https://mc-api-j0rn.onrender.com/api/list"
    }

    for name, url in endpoints.items():
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            print(f"[✓] {name} - Success")
            pprint(response.json()[:2])  # Print just first 2 entries for brevity
        except Exception as e:
            print(f"[✗] {name} - Failed: {e}")
        print("-" * 50)

def test_local_api():
    print("\nTesting local moneycontrol-api package...\n")
    try:
        from moneycontrol import moneycontrol_api as mc

        print("[✓] Imported local package successfully\n")

        print("[News]")
        pprint(mc.get_news()[:2])

        print("\n[Latest News]")
        pprint(mc.get_latest_news()[:2])

        print("\n[Business News]")
        pprint(mc.get_business_news()[:2])

    except ImportError as e:
        print("[✗] Failed to import package. Did you install it with pip?")
        print("    ➤ Try: pip install moneycontrol-api")
    except Exception as e:
        print(f"[✗] Error occurred during local API testing: {e}")

if __name__ == "__main__":
    test_remote_api()
    test_local_api()


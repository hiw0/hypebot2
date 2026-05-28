import os
import sys


def main() -> int:
    # Avoid importing redis if REDIS_URL is not set; default to success to not block boot
    url = os.environ.get("REDIS_URL", "").strip()
    require = os.environ.get("REQUIRE_REDIS", "0").strip() == "1"
    if not url:
        # No URL configured; consider healthy so platform can start the container
        return 0
    try:
        import redis  # type: ignore
        r = redis.Redis.from_url(url, decode_responses=True)
        r.ping()
        return 0
    except Exception as e:
        # Print error for 'docker inspect' visibility
        print(f"Redis healthcheck failed: {e}")
        # Only mark unhealthy when explicitly required
        return 1 if require else 0


if __name__ == "__main__":
    sys.exit(main())

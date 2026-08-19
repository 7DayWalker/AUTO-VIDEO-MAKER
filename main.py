"""
AUTO Video Maker - Main Entry Point

Runs a system health check to verify the project is correctly configured.
"""

import sys

from app.config import (
    APP_NAME,
    CURRENT_PHASE,
    ensure_directories_exist,
    get_config_summary,
)


def run_health_check() -> bool:
    """
    Run the Phase 0 system health check.

    Verifies:
    - Configuration loads successfully
    - Required project directories exist (creates them if needed)

    Returns:
        True if all checks pass, False otherwise.
    """
    print("=" * 50)
    print(f"  {APP_NAME}")
    print("=" * 50)
    print()

    # 1. Show configuration summary
    config = get_config_summary()
    print("[CONFIG]")
    print(f"  Phase:       {config['current_phase']}")
    print(f"  Environment: {config['environment']}")
    print(f"  Log Level:   {config['log_level']}")
    print(f"  Project Root: {config['project_root']}")
    print()

    # 2. Verify directories
    print("[DIRECTORIES]")
    dir_results = ensure_directories_exist()
    all_ok = True
    for name, exists in dir_results.items():
        status = "OK" if exists else "MISSING"
        print(f"  {name:15s} ... {status}")
        if not exists:
            all_ok = False
    print()

    # 3. Final result
    if all_ok:
        print("[RESULT] All health checks PASSED.")
        print(f"  {APP_NAME} is running.")
        print(f"  Current phase: {CURRENT_PHASE}")
    else:
        print("[RESULT] Some health checks FAILED.")
        print("  Please check the output above for issues.")

    print()
    print("=" * 50)
    return all_ok


if __name__ == "__main__":
    success = run_health_check()
    sys.exit(0 if success else 1)

"""Backward-compatible entry point for the seed taxonomy upgrader."""

from seed_taxonomy_upgrade import main, upgrade_seed_taxonomy


def upgrade_taxonomy(db_path, apply=False, backup_dir=None):
    """Compatibility alias kept for existing Synology deployment commands."""
    return upgrade_seed_taxonomy(db_path, apply=apply, backup_dir=backup_dir)


if __name__ == "__main__":
    main()

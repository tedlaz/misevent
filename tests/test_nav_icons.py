from webapp.labels import NAV


def test_nav_icons_unique():
    """Στο μενού «μόνο εικονίδια» το εικονίδιο είναι το μόνο που ξεχωρίζει μια σελίδα."""
    icons = [i for _, i, *_ in NAV]
    assert len(icons) == len(set(icons)), icons

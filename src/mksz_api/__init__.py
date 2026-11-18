"""mksz_api: nba_api-style access to Hungarian Handball Federation (MKSZ) data.

    from mksz_api.endpoints import MatchFinder, BoxScore, PlayByPlay
    games = MatchFinder(season="2026/27", league="NBI", gender="F").get_data_frames()[0]
"""

__version__ = "0.1.0"

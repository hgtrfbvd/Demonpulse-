"""
repositories/__init__.py — DemonPulse V8 Repository Package
============================================================
Canonical data-access layer for DemonPulse.

Each module in this package provides a repository class (or module-level
functions) for one logical domain.  All database access outside of low-level
helpers should go through these repositories.

Quick imports:
    from repositories.races_repo      import RacesRepo
    from repositories.runners_repo    import RunnersRepo
    from repositories.results_repo    import ResultsRepo
    from repositories.predictions_repo import PredictionsRepo
    from repositories.learning_repo   import LearningRepo
    from repositories.backtesting_repo import BacktestingRepo
    from repositories.users_repo      import UsersRepo
    from repositories.logs_repo       import LogsRepo
    from repositories.meetings_repo   import MeetingsRepo
"""

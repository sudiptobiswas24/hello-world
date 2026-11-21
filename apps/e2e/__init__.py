"""
Tests that cross every module at once.

Not an app: nothing here has models, and nothing imports it. It lives
apart because the test in it depends on every module, and putting it
inside any one of them would claim a dependency that module is not
allowed to have.
"""

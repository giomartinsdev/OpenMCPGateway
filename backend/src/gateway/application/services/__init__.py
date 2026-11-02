"""Use cases: what a request *does*, with no HTTP in sight.

A route parses a body, asks the gate for a permission, and calls one function
here. That is why the UI and `omg apply` are equivalent — both end up in the same
place — and why each of these is testable without a request.
"""

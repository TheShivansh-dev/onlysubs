"""
Bulk GUID generator — run once to pre-populate Data/GUIDs.xlsx with 10,000 GUIDs.
Distributes GUIDs evenly across all 12 subscription plans.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from Handlers.config import SUBSCRIPTION_PLANS, generate_guids

TOTAL = 10_000
plans = list(SUBSCRIPTION_PLANS.items())
n = len(plans)

base, extra = divmod(TOTAL, n)
counts = [base + (1 if i < extra else 0) for i in range(n)]

print(f"Generating {TOTAL} GUIDs across {n} plans...\n")

total_generated = 0
for (plan_key, plan), count in zip(plans, counts):
    month_code = plan['month_code']
    generated = generate_guids(plan_key, month_code, count)
    total_generated += generated
    print(f"  {plan_key:12s}  month_code={month_code}  count={generated}")

print(f"\nDone. Total GUIDs generated: {total_generated}")
print("Saved to: Data/GUIDs.xlsx")

Now let me run the exact pytest command specified in the ticket to observe the current failures.
Confirmed 15 failed / 17 passed, matching the ticket. Let me inspect other Toss call sites to ensure the guards don't break unrelated code.
No other Toss mutation call sites. Now I'll repair the two GET guards to handle non-string methods (ticket item 9).
Now I'll rewrite the TX00 test module as ordinary sync pytest functions per the ticket (items 5–7, 9–10).

These four tests assert that a connection to a black-holed address times out.
The grading container has no route off the machine, so the connection is refused
immediately instead of hanging, and the assertion fails. They fail identically
with the gold patch applied and without it, which is the test this project uses
for an environment-dependent id: excluded because gold cannot pass them here,
never to make a failing result look better. Verified on both `--network none`
and the internal network.

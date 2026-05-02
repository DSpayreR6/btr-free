btr-free
Predict how much space will be freed before deleting Btrfs snapshots.
The Problem
When managing Btrfs snapshots, it is currently impossible to know how much space will be freed before actually deleting a set of snapshots. The exclusive value shown per snapshot only reflects what would be freed if that single snapshot were deleted in isolation. Summing these values across multiple snapshots is misleading – the actual freed space can be significantly higher due to shared extents between snapshots.
Why This Happens – the Mosaic Principle
Every Btrfs snapshot can be thought of as a "map" showing which data blocks exist at that point in time. When you overlay all snapshots and the live system, you get distinct regions – each region has a unique "signature": which snapshots and the live system reference it.
A block is only freed when all of its referencing snapshots are in the deletion set – and it is not referenced by the live system. This means:

There is a permanent "continent" – blocks referenced by every snapshot and the live system – that can never be freed by deleting snapshots
There is addressable fluctuation – blocks that existed at some point but are no longer in the live system – this is the only space recoverable through snapshot deletion
Individual excl values only capture blocks exclusive to a single snapshot, missing all the shared fluctuation that becomes free when a group is deleted together

Example
Selecting 16 snapshots on a real system showed:
SpaceSum of individual excl values (lower bound)32.63 GiBActual space freed (calculated)614.36 GiB
That is a ~19x difference.
How It Works
Btrfs qgroup hierarchies solve this exactly. By assigning a group of snapshots to a temporary qgroup, running quota rescan and reading the excl value of that group, you get the true amount of space that would be freed by deleting exactly that group.
Feature Request
This functionality should be integrated into Btrfs Assistant. A feature request has been filed:
👉 https://gitlab.com/btrfs-assistant/btrfs-assistant/-/issues

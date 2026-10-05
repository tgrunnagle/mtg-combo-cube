# Documentation

Documentation for the MTG Combo Cube builder. For setup and usage, see the
[project README](../README.md).

## Index

- **[architecture.md](architecture.md)**: the system as implemented.
  - [Purpose](architecture.md#purpose)
  - [System overview](architecture.md#system-overview)
  - [Package layout](architecture.md#package-layout)
  - [Data pipeline](architecture.md#data-pipeline)
  - [The ILP optimizer](architecture.md#the-ilp-optimizer)
  - [The greedy builder](architecture.md#the-greedy-builder)
  - [Outputs](architecture.md#outputs)
  - [Testing](architecture.md#testing)
  - [Known limitations](architecture.md#known-limitations)

## Subdirectories

- **[plans/](plans/README.md)**: plans and design documents from each round of development.
  They are point-in-time records and have their own index.

## Maintaining these docs

- When a change alters how the system works, update [architecture.md](architecture.md) as part
  of it.
- Add each new document in this folder to the index above.

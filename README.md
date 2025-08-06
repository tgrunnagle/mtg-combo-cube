# MTG Combo Cube

A tool for building Magic: The Gathering combo cubes by analyzing card popularity in known combos and optimizing card selection.

## Usage

### Building a Cube

```bash
run -c X -r Y
```

Builds a cube of size `X` and checks the combo count of the result. You can also specify a golden ratio `Y` (top cards to almost included cards) with the `-r` flag.

## How It Works

The cube building process follows these steps:

1. **Find Popular Combos**: Identifies the top 5000 known combos by popularity and tracks how many times each card appears in combos

2. **Select Core Cards**: Takes a list of the top cards by count that is smaller than the target cube count, leaving room for 'off by one' combos

3. **Fill Remaining Slots**: Finds 'almost included' combos for the current card list and uses the missing cards to fill up the cube to the target size

4. **Determine Final Combos**: Calculates which combos are possible with the final cube composition

5. **Remove Dead Cards**: Identifies and removes 'dead cards' (cards not included in any combos) and adds more 'almost included' cards to replace them

This process ensures the cube maximizes combo potential while maintaining the desired size constraint.


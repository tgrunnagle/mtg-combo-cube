import asyncio
from commander_spellbook import CommanderSpellbook
from variant_tracker import VariantTracker
from cube_builder import CubeBuilder
import json
import argparse

async def main(cube_size: int, ratio_top_cards: float):
    spellbook = CommanderSpellbook()
    variant_tracker = VariantTracker()
    async for variant in spellbook.get_variants(max_cards_in_combo=3, max_pages=1):
        variant_tracker.process_variant(variant)
    
    print("Top cards:")
    print(variant_tracker.get_top_cards(10))
    print("Top required cards:")
    print(await variant_tracker.get_top_required_cards(10))
    cube_builder = CubeBuilder()
    cube = await cube_builder.build_cube(spellbook, cube_size, ratio_top_cards)
    print(json.dumps(cube, indent=2))

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=100)
    argparser.add_argument("-r", "--ratio-top-cards", type=float, default=0.7)
    args = argparser.parse_args()
    asyncio.run(main(args.cube_size, args.ratio_top_cards))
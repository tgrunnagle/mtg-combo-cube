import asyncio
from commander_spellbook import CommanderSpellbook
from variant_tracker import VariantTracker
from cube_builder import CubeBuilder
import json
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main(cube_size: int, ratio_top_cards: float):
    spellbook = CommanderSpellbook()
    variant_tracker = VariantTracker()
    async for variant in spellbook.get_variants(max_cards_in_combo=3, max_pages=10):
        variant_tracker.process_variant(variant)
    
    cube_builder = CubeBuilder()
    cube = await cube_builder.build_cube(spellbook, cube_size, ratio_top_cards)
    logger.info(f"Cube list:\n{json.dumps(cube, indent=2)}")
    with open("cube.txt", "w") as f:
        f.write("\n".join(cube))

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=100)
    argparser.add_argument("-r", "--ratio-top-cards", type=float, default=0.7)
    args = argparser.parse_args()
    asyncio.run(main(args.cube_size, args.ratio_top_cards))
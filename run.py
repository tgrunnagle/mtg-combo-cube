import asyncio
from commander_spellbook import CommanderSpellbook
from variant_tracker import VariantTracker
from cube_builder import CubeBuilder
import json
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main(cube_size: int):
    spellbook = CommanderSpellbook()
    cube_builder = CubeBuilder()
    cube = await cube_builder.build_cube(spellbook, cube_size)
    with open("cube.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(cube))

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=100)
    args = argparser.parse_args()
    asyncio.run(main(args.cube_size))
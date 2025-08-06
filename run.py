import asyncio
from cube_builder import CubeBuilder
import argparse
import logging

logger = logging.getLogger(__name__)

async def main(cube_size: int, test: bool, output_file: str):
    cube_builder = CubeBuilder(cube_size)
    await cube_builder.build_cube()
    logger.info(f"Built cube of size {len(cube_builder.get_cube())}")
    
    if test:
        logger.info("Testing cube...")
        combos = await cube_builder.get_combos()
        logger.info(f"Found {len(combos)} combos in cube")

    logger.info("Removing dead cards...")
    await cube_builder.remove_dead_cards()
    count_removed = cube_size - len(cube_builder.get_cube())
    logger.info(f"Removed {count_removed} dead cards")
    logger.info("Adding almost included cards...")
    await cube_builder.add_almost_included()

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(cube_builder.get_cube()))

    if test:
        combos = await cube_builder.get_combos()
        logger.info(f"Found {len(combos)} combos in cube after removing dead cards and adding almost included cards")

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=100)
    argparser.add_argument("-t", "--test", action="store_true")
    argparser.add_argument("-o", "--output-file", type=str, default='cube.txt')
    argparser.add_argument("-d", "--debug", action="store_true")
    args = argparser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    asyncio.run(main(args.cube_size, args.test, args.output_file))
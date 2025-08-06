import asyncio
from cube_builder import CubeBuilder
import argparse
import logging

logger = logging.getLogger(__name__)

async def build_cube(cube_size: int, golden_ratio: float) -> tuple[list[str], int]:
    cube_builder = CubeBuilder(cube_size)
    logger.info(f"Building cube with golden ratio of {golden_ratio}...")
    await cube_builder.build_cube(golden_ratio=golden_ratio)
    logger.info(f"Built cube of size {len(cube_builder.get_cube())}")
    
    logger.info("Testing cube...")
    combos = await cube_builder.get_combos()
    logger.info(f"Found {len(combos)} combos in cube")

    logger.info("Removing dead cards...")
    await cube_builder.remove_dead_cards()
    count_removed = cube_size - len(cube_builder.get_cube())
    logger.info(f"Removed {count_removed} dead cards")
    logger.info("Adding almost included cards...")
    await cube_builder.add_almost_included()

    combos = await cube_builder.get_combos()
    logger.info(f"Found {len(combos)} combos in cube after removing dead cards and adding almost included cards")
    
    return cube_builder.get_cube(), len(combos)

async def main(cube_size: int, output_file: str, golden_ratio: float | None = None):
    golden_ratio = golden_ratio if golden_ratio else CubeBuilder.GOLDEN_RATIO
    result = await build_cube(cube_size, golden_ratio)
    
    logger.info(f"Found {result[1]} combos with golden ratio {golden_ratio}")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(result[0]))

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=100)
    argparser.add_argument("-r", "--ratio", type=float)
    argparser.add_argument("-o", "--output-file", type=str, default='cube.txt')
    argparser.add_argument("-d", "--debug", action="store_true")
    args = argparser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    asyncio.run(main(args.cube_size, args.output_file, golden_ratio=args.ratio))
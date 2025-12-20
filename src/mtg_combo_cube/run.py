import logging

from mtg_combo_cube.cube_builder import CubeBuilder

logger = logging.getLogger(__name__)


async def build_cube(cube_size: int, golden_ratio: float) -> tuple[list[str], int]:
    cube_builder = CubeBuilder(cube_size)
    logger.info(
        f"Building {cube_size} card cube with golden ratio of {golden_ratio}..."
    )
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
    logger.info(
        f"Found {len(combos)} combos in cube after removing dead cards and adding almost included cards"
    )

    return cube_builder.get_cube(), len(combos)


async def run(cube_size: int, output_file: str, golden_ratio: float | None = None):
    golden_ratio = golden_ratio if golden_ratio else CubeBuilder.GOLDEN_RATIO
    result = await build_cube(cube_size, golden_ratio)

    logger.info(f"Found {result[1]} combos with golden ratio {golden_ratio}")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(result[0]))

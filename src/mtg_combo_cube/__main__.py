import argparse
import asyncio
import logging

from mtg_combo_cube.run import run

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("-c", "--cube-size", type=int, default=300)
    argparser.add_argument("-r", "--ratio", type=float)
    argparser.add_argument("-o", "--output-file", type=str, default='cube.txt')
    argparser.add_argument("-d", "--debug", action="store_true")
    args = argparser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    asyncio.run(run(args.cube_size, args.output_file, golden_ratio=args.ratio))
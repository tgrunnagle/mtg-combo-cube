import asyncio
from commander_spellbook import CommanderSpellbook
from variant_tracker import VariantTracker

async def main():
    spellbook = CommanderSpellbook()
    variant_tracker = VariantTracker()
    async for variant in spellbook.get_variants(max_cards=3, max_pages=1):
        variant_tracker.process_variant(variant)
    
    print("Top cards:")
    print(variant_tracker.get_top_cards(10))
    print("Top required cards:")
    print(await variant_tracker.get_top_required_cards(10))

asyncio.run(main())
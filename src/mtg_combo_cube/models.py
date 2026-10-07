"""Models of the card data: Commander Spellbook responses and Scryfall card attributes."""

from dataclasses import dataclass

from pydantic import BaseModel, Field

EM_DASH = "—"  # separates the card types from the subtypes in a Scryfall type line


@dataclass(frozen=True)
class CardAttributes:
    """
    What a build knows about a card, from Scryfall: the data behind the color balance, the
    card mix rules and the color and card mix statistics.
    """

    color_identity: str  # WUBRG letters in that order, "" for colorless
    # The Scryfall type line; a multi-faced card has one per face, joined with " // "
    type_line: str = ""
    # Scryfall's cmc: the front face's for a double-faced card, both halves for a split card
    mana_value: float = 0.0

    @property
    def types(self) -> frozenset[str]:
        """
        The types of the card's front face: the words of its type line before the em dash,
        so "Legendary Artifact Creature" gives Legendary, Artifact and Creature.
        """
        front = self.type_line.split(" // ")[0]
        return frozenset(front.split(EM_DASH)[0].split())

    @property
    def is_multicolor(self) -> bool:
        return len(self.color_identity) >= 2

    @property
    def is_colorless(self) -> bool:
        return not self.color_identity


# What a card without Scryfall data counts as: colorless, typeless, mana value 0
UNKNOWN_CARD = CardAttributes(color_identity="")


class Card(BaseModel):
    id: int
    name: str
    spoiler: bool
    oracle_id: str = Field(alias="oracleId")
    type_line: str = Field(alias="typeLine")
    image_uri_back_png: str | None = Field(alias="imageUriBackPng")
    image_uri_front_png: str | None = Field(alias="imageUriFrontPng")
    image_uri_back_large: str | None = Field(alias="imageUriBackLarge")
    image_uri_back_small: str | None = Field(alias="imageUriBackSmall")
    image_uri_back_normal: str | None = Field(alias="imageUriBackNormal")
    image_uri_front_large: str | None = Field(alias="imageUriFrontLarge")
    image_uri_front_small: str | None = Field(alias="imageUriFrontSmall")
    image_uri_front_normal: str | None = Field(alias="imageUriFrontNormal")
    image_uri_back_art_crop: str | None = Field(alias="imageUriBackArtCrop")
    image_uri_front_art_crop: str | None = Field(alias="imageUriFrontArtCrop")


class CardUse(BaseModel):
    card: Card
    quantity: int
    zone_locations: list[str] = Field(alias="zoneLocations")
    exile_card_state: str = Field(alias="exileCardState")
    must_be_commander: bool = Field(alias="mustBeCommander")
    library_card_state: str = Field(alias="libraryCardState")
    graveyard_card_state: str = Field(alias="graveyardCardState")
    battlefield_card_state: str = Field(alias="battlefieldCardState")


class Template(BaseModel):
    id: int
    name: str
    scryfall_api: str | None = Field(default=None, alias="scryfallApi")
    scryfall_query: str | None = Field(default=None, alias="scryfallQuery")


class Requirement(BaseModel):
    quantity: int
    template: Template
    zone_locations: list[str] = Field(alias="zoneLocations")
    exile_card_state: str = Field(alias="exileCardState")
    must_be_commander: bool = Field(alias="mustBeCommander")
    library_card_state: str = Field(alias="libraryCardState")
    graveyard_card_state: str = Field(alias="graveyardCardState")
    battlefield_card_state: str = Field(alias="battlefieldCardState")


class Feature(BaseModel):
    id: int
    name: str
    status: str
    uncountable: bool


class Produces(BaseModel):
    feature: Feature
    quantity: int


class Prices(BaseModel):
    tcgplayer: str
    cardmarket: str
    cardkingdom: str


class Legalities(BaseModel):
    brawl: bool
    predh: bool
    legacy: bool
    modern: bool
    pauper: bool
    pioneer: bool
    vintage: bool
    standard: bool
    commander: bool
    premodern: bool
    oathbreaker: bool
    pauper_commander: bool = Field(alias="pauperCommander")
    pauper_commander_main: bool = Field(alias="pauperCommanderMain")


class ComboReference(BaseModel):
    id: int


class Variant(BaseModel):
    id: str
    of: list[ComboReference]
    uses: list[CardUse]
    notes: str
    prices: Prices
    status: str
    spoiler: bool
    identity: str
    includes: list[ComboReference]
    produces: list[Produces]
    requires: list[Requirement]
    legalities: Legalities
    popularity: int | None
    bracket_tag: str = Field(alias="bracketTag")
    description: str
    mana_needed: str = Field(alias="manaNeeded")
    variant_count: int = Field(alias="variantCount")
    mana_value_needed: int = Field(alias="manaValueNeeded")
    easy_prerequisites: str = Field(alias="easyPrerequisites")
    notable_prerequisites: str = Field(alias="notablePrerequisites")

    model_config = {"populate_by_name": True}

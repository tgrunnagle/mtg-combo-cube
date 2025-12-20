from typing import Optional

from pydantic import BaseModel, Field


class Card(BaseModel):
    id: int
    name: str
    spoiler: bool
    oracle_id: str = Field(alias="oracleId")
    type_line: str = Field(alias="typeLine")
    image_uri_back_png: Optional[str] = Field(alias="imageUriBackPng")
    image_uri_front_png: Optional[str] = Field(alias="imageUriFrontPng")
    image_uri_back_large: Optional[str] = Field(alias="imageUriBackLarge")
    image_uri_back_small: Optional[str] = Field(alias="imageUriBackSmall")
    image_uri_back_normal: Optional[str] = Field(alias="imageUriBackNormal")
    image_uri_front_large: Optional[str] = Field(alias="imageUriFrontLarge")
    image_uri_front_small: Optional[str] = Field(alias="imageUriFrontSmall")
    image_uri_front_normal: Optional[str] = Field(alias="imageUriFrontNormal")
    image_uri_back_art_crop: Optional[str] = Field(alias="imageUriBackArtCrop")
    image_uri_front_art_crop: Optional[str] = Field(alias="imageUriFrontArtCrop")


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

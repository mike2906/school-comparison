"""Unit tests for the free-text → controlled-vocabulary mapping (P1.9)."""

from app.utils.facility_vocabulary import (
    APPROACH_VOCAB,
    FACILITY_VOCAB,
    PROGRAM_VOCAB,
    canonical_tags,
)


class TestFacilityMapping:
    def test_bulgarian_and_english_free_text_map_to_same_tag(self):
        assert canonical_tags(["Библиотека", "Library", "читалня"], FACILITY_VOCAB) == ["library"]
        assert canonical_tags(["физкултурен салон"], FACILITY_VOCAB) == ["sports_facilities"]
        assert canonical_tags(["Компютърни кабинети"], FACILITY_VOCAB) == ["computer_lab"]
        assert canonical_tags(["STEM център"], FACILITY_VOCAB) == ["computer_lab"]
        assert canonical_tags(["училищен транспорт", "shuttle"], FACILITY_VOCAB) == ["transportation"]

    def test_canteen_short_stem_matches_only_as_exact_token(self):
        assert canonical_tags(["стол"], FACILITY_VOCAB) == ["cafeteria"]
        assert canonical_tags(["столова"], FACILITY_VOCAB) == ["cafeteria"]
        # "престол" / "столче" must not be dragged in by a bare substring.
        assert canonical_tags(["престол"], FACILITY_VOCAB) == []

    def test_unmapped_free_text_yields_no_tag(self):
        assert canonical_tags(["3D принтер", "VR очила", "методичен кабинет"], FACILITY_VOCAB) == []

    def test_already_canonical_values_pass_through(self):
        for tag in ("sports_facilities", "cafeteria", "library", "computer_lab", "transportation"):
            assert canonical_tags([tag], FACILITY_VOCAB) == [tag]

    def test_legacy_underscore_key_maps_to_canonical_tag(self):
        # seed_data.py stores the legacy underscore key "sports_field"; it must still
        # match the spaced "sports field" phrase in the vocab.
        assert canonical_tags(["sports_field"], FACILITY_VOCAB) == ["sports_facilities"]

    def test_multiple_values_collapse_and_sort(self):
        assert canonical_tags(["библиотека", "физкултурен салон"], FACILITY_VOCAB) == [
            "library",
            "sports_facilities",
        ]


class TestProgramMapping:
    def test_sports_and_music_and_arts(self):
        assert canonical_tags(["футбол", "Волейбол"], PROGRAM_VOCAB) == ["sports_program"]
        assert canonical_tags(["спортна програма", "sports club"], PROGRAM_VOCAB) == ["sports_program"]
        assert canonical_tags(["вокална група", "пиано"], PROGRAM_VOCAB) == ["music_program"]
        assert canonical_tags(["народни танци", "балет"], PROGRAM_VOCAB) == ["arts_program"]

    def test_transport_is_not_a_sports_program(self):
        # "транспорт"/"transport" contain the substring "спорт"/"sport" mid-word.
        assert canonical_tags(["транспортна логистика"], PROGRAM_VOCAB) == []
        assert canonical_tags(["transport and logistics"], PROGRAM_VOCAB) == []

    def test_choreography_does_not_leak_into_music(self):
        # "хореография" contains the substring "хор" but is a dance program, not choir.
        assert canonical_tags(["хореография"], PROGRAM_VOCAB) == ["arts_program"]

    def test_meals_and_extended_day(self):
        assert canonical_tags(["Кетъринг"], PROGRAM_VOCAB) == ["meals_provided"]
        assert canonical_tags(["здравословно хранене"], PROGRAM_VOCAB) == ["meals_provided"]
        # English-only meal text without "meal"/"lunch" still maps.
        assert canonical_tags(["Food from children's caterer"], PROGRAM_VOCAB) == ["meals_provided"]
        assert canonical_tags(["Morning breakfast", "afternoon snack"], PROGRAM_VOCAB) == ["meals_provided"]
        assert canonical_tags(["целодневна организация"], PROGRAM_VOCAB) == ["extended_day"]

    def test_security_is_not_meals(self):
        # "охрана"/"съхранение" contain "хран" mid-word but are not meals.
        assert canonical_tags(["охрана", "съхранение на данни"], PROGRAM_VOCAB) == []

    def test_already_canonical_program_values_pass_through(self):
        for tag in ("music_program", "sports_program", "arts_program", "extended_day", "meals_provided"):
            assert canonical_tags([tag], PROGRAM_VOCAB) == [tag]


class TestApproachMapping:
    def test_montessori_waldorf_ib(self):
        assert canonical_tags(["Montessori"], APPROACH_VOCAB) == ["montessori"]
        assert canonical_tags(["Waldorf pedagogy"], APPROACH_VOCAB) == ["waldorf"]
        assert canonical_tags(["International Baccalaureate (IB)"], APPROACH_VOCAB) == ["ib_program"]
        assert canonical_tags(["IB"], APPROACH_VOCAB) == ["ib_program"]
        assert canonical_tags(["проектно-базирано обучение"], APPROACH_VOCAB) == ["project_based"]

    def test_ib_short_token_matches_only_as_exact_token(self):
        # "библиотека" contains the letters "ib" but must not become an IB programme.
        assert canonical_tags(["библиотека"], APPROACH_VOCAB) == []

    def test_play_based_learning_is_not_project_based(self):
        assert canonical_tags(["play-based learning"], APPROACH_VOCAB) == []
        assert canonical_tags(["project-based learning"], APPROACH_VOCAB) == ["project_based"]


class TestSubstringFalsePositiveGuards:
    def test_system_is_not_a_computer_lab(self):
        # "stem"/"стем" are substrings of "system"/"система"; the mapper must not match.
        assert canonical_tags(["operating system", "school system"], FACILITY_VOCAB) == []
        assert canonical_tags(["система за видеонаблюдение"], FACILITY_VOCAB) == []
        # But a real STEM room (latin word + cyrillic noun) still maps.
        assert canonical_tags(["STEM център"], FACILITY_VOCAB) == ["computer_lab"]

import unittest

from word_suggestions import WORDS, completion_suffix, current_word, suggest


class WordSuggestionTests(unittest.TestCase):
    def test_suggests_common_words_first(self):
        self.assertEqual(suggest("th")[:2], ["the", "that"])
        self.assertIn("medicine", suggest("medi"))

    def test_no_suggestions_without_a_prefix(self):
        self.assertEqual(suggest(""), [])

    def test_exact_word_is_not_suggested_again(self):
        self.assertNotIn("the", suggest("the"))

    def test_limit_is_respected(self):
        self.assertLessEqual(len(suggest("s", limit=4)), 4)

    def test_pronoun_i_is_capitalized(self):
        self.assertIn("I'm", suggest("i'"))

    def test_current_word_reads_the_partial_word(self):
        self.assertEqual(current_word("I need my medi"), "medi")
        self.assertEqual(current_word("Hello "), "")
        self.assertEqual(current_word("don'"), "don'")

    def test_completion_keeps_capitalization(self):
        self.assertEqual(completion_suffix("Hel", "hello"), "lo")
        self.assertEqual(completion_suffix("HEL", "hello"), "LO")
        self.assertEqual(completion_suffix("h", "hello"), "ello")

    def test_word_list_has_no_duplicates(self):
        self.assertEqual(len(WORDS), len(set(WORDS)))
        self.assertGreater(len(WORDS), 500)


if __name__ == "__main__":
    unittest.main()

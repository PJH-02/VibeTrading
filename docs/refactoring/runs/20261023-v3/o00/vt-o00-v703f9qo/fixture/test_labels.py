import unittest
from labels import normalize_labels

class LabelTests(unittest.TestCase):
    def test_trim_and_empty(self):
        self.assertEqual(normalize_labels(["  Alpha ", "", " ", "Beta"]), ["Alpha", "Beta"])
    def test_stable_dedupe(self):
        self.assertEqual(normalize_labels([" b ", "A", "B", " a ", "C"]), ["b", "A", "C"])
    def test_unicode_casefold(self):
        self.assertEqual(normalize_labels(["Straße", "STRASSE", "한글", " 한글 "]), ["Straße", "한글"])
    def test_empty(self):
        self.assertEqual(normalize_labels([]), [])
    def test_no_mutation(self):
        source = [" x ", "X"]
        self.assertEqual(normalize_labels(source), ["x"])
        self.assertEqual(source, [" x ", "X"])
    def test_invalid_element(self):
        with self.assertRaises(TypeError): normalize_labels(["ok", None])
    def test_invalid_input(self):
        with self.assertRaises(TypeError): normalize_labels("hello")

if __name__ == "__main__": unittest.main()

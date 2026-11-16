import unittest


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def buggy_percentage(part, total):
    # Bug: returns wrong calculation
    return (part / total) * 10


class TestMathModule(unittest.TestCase):

    def test_add_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_add_zero(self):
        self.assertEqual(add(5, 0), 5)

    def test_subtract_positive(self):
        self.assertEqual(subtract(10, 4), 6)

    def test_subtract_zero(self):
        self.assertEqual(subtract(7, 0), 7)

    def test_multiply_positive(self):
        self.assertEqual(multiply(3, 4), 12)

    def test_multiply_zero(self):
        self.assertEqual(multiply(8, 0), 0)

    def test_failing_percentage(self):
        # 50 out of 100 should be 50%, but buggy_percentage returns 5.0
        self.assertEqual(buggy_percentage(50, 100), 50.0)

    def test_subtract_negative(self):
        self.assertEqual(subtract(2, 5), -3)


if __name__ == "__main__":
    unittest.main()

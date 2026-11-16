import unittest

from calc import add, average, percentage


class TestCalc(unittest.TestCase):

    def test_add_1(self):
        self.assertEqual(add(1, 1), 2)

    def test_add_2(self):
        self.assertEqual(add(2, 2), 4)

    def test_add_3(self):
        self.assertEqual(add(3, 3), 6)

    def test_add_4(self):
        self.assertEqual(add(4, 4), 8)

    def test_add_5(self):
        self.assertEqual(add(5, 5), 10)

    def test_add_6(self):
        self.assertEqual(add(6, 6), 12)

    def test_add_7(self):
        self.assertEqual(add(7, 7), 14)

    def test_add_8(self):
        self.assertEqual(add(8, 8), 16)

    def test_percentage_failing(self):
        self.assertEqual(percentage(50, 100), 50.0)

    def test_average_1(self):
        self.assertEqual(average([1, 1]), 1)

    def test_average_2(self):
        self.assertEqual(average([2, 2]), 2)

    def test_average_3(self):
        self.assertEqual(average([3, 3]), 3)

    def test_average_4(self):
        self.assertEqual(average([4, 4]), 4)

    def test_average_5(self):
        self.assertEqual(average([5, 5]), 5)

    def test_average_6(self):
        self.assertEqual(average([6, 6]), 6)

    def test_average_7(self):
        self.assertEqual(average([7, 7]), 7)

    def test_average_8(self):
        self.assertEqual(average([8, 8]), 8)


if __name__ == "__main__":
    unittest.main()

# class definition
class Guitar:
    def __init__(self, brand, model, price):
        self.brand = brand
        self.model = model
        self.price = price

    def display_info(self):
        print(f"Brand: {self.brand}, Model: {self.model}, Price: ${self.price}")

    # Methods
    def play(self):
        print(f"{self.brand} {self.model} is playing a transcendental melody.")


# Object instantiation
guitar1 = Guitar("Schecter", "Hell Raiser", 1000)
guitar2 = Guitar("Alhambra", "Semi Concert", 500)

# Calling methods
guitar1.play()
guitar2.play()

print(guitar1.brand)
print(guitar2.model)

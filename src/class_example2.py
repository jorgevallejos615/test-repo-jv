class TemperatureReading:
    default_unit = "Celsius"  # class-wide (static) attribute

    def __init__(self, celsius):
        self.__celsius = celsius  # celsius: public: _celsius=protected attribute __celsius=private attribute > mangling: the object will read it as _TemperatureReading__celsius

    # Property methods
    @property
    def celsius(self):
        return self.__celsius

    @celsius.setter
    def celsius(self, celsius):
        if celsius < -273.15:
            raise ValueError("Temperature cannot be below absolute zero.")
        self.__celsius = celsius

    @classmethod
    def construct_from_fahrenheit(cls, fahrenheit):
        celsius = (fahrenheit - 32) * 5 / 9
        return cls(celsius)

    @staticmethod
    def validate_celsius(celsius):
        return celsius >= -273.15


temp1 = TemperatureReading(25)  # Object instantiation
print(temp1.celsius)  # Accessing property method
temp2 = TemperatureReading.construct_from_fahrenheit(
    70
)  # Object instantiation using class method
print(temp2.celsius)  # Accessing property method

print(TemperatureReading.validate_celsius(25))  # static call
print(TemperatureReading.validate_celsius(-300))  # static call

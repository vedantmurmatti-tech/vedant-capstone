import pyttsx3


class EsmereldaVoice:

    def __init__(self):

        self.engine = pyttsx3.init()

        # Speech speed
        self.engine.setProperty("rate", 175)

        # Volume
        self.engine.setProperty("volume", 1.0)

    def speak(self, text):

        print(f"\nEsmerelda: {text}")

        self.engine.say(text)
        self.engine.runAndWait()


if __name__ == "__main__":

    voice = EsmereldaVoice()

    voice.speak(
        "Good evening. Esmerelda is online."
    )

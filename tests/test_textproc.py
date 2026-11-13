"""Run with:  .venv/bin/python -m unittest discover -s tests -v"""

import unittest

from dictation import textproc as tp


class VoiceCommands(unittest.TestCase):
    def test_scratch_that_drops_previous_sentence(self):
        self.assertEqual(
            tp.process("So I think we should ship the release on Friday. Scratch that. "
                       "Let's ship it on Monday morning."),
            "Let's ship it on Monday morning.")

    def test_scratch_that_mid_clause_keeps_earlier_sentences(self):
        self.assertEqual(
            tp.process("Hi team. The meeting is at 2, scratch that, the meeting is at 3."),
            "Hi team. The meeting is at 3.")

    def test_borra_eso(self):
        self.assertEqual(tp.process("Llego el lunes. Borra eso. Llego el martes."),
                         "Llego el martes.")

    def test_new_line_es(self):
        self.assertEqual(
            tp.process("Hola equipo. Nueva línea. Por favor confirmen."),
            "Hola equipo.\nPor favor confirmen.")

    def test_new_paragraph_en_capitalises_next(self):
        self.assertEqual(tp.process("Thanks, new paragraph, see you soon."),
                         "Thanks\n\nSee you soon.")

    def test_punto_y_aparte(self):
        self.assertEqual(tp.process("Ya quedó punto y aparte ahora lo otro."),
                         "Ya quedó.\n\nAhora lo otro.")

    def test_disabled(self):
        text = "Hola. Nueva línea. Adiós."
        self.assertEqual(tp.process(text, voice_commands=False), text)


class Backtrack(unittest.TestCase):
    def test_spanish_full_output_from_whisper(self):
        self.assertEqual(
            tp.process("Hola equipo, la reunión es mañana a las 2, no, a las 3. Nueva línea. "
                       "Por favor confirmen su asistencia."),
            "Hola equipo, la reunión es mañana a las 3.\nPor favor confirmen su asistencia.")

    def test_english_actually(self):
        self.assertEqual(tp.process("Let's do coffee at 2 actually 3."),
                         "Let's do coffee at 3.")

    def test_word_numbers(self):
        self.assertEqual(tp.process("Nos vemos a las dos, perdón, a las tres."),
                         "Nos vemos a las tres.")

    def test_does_not_touch_plain_actually(self):
        text = "I actually enjoyed it, no matter what 2 people said."
        self.assertEqual(tp.process(text), text)


class Fillers(unittest.TestCase):
    def test_english(self):
        self.assertEqual(tp.process("Um, so I think, uh, we should go.", language="en"),
                         "So I think, we should go.")

    def test_spanish(self):
        self.assertEqual(tp.process("Eh, mmm, creo que sí.", language="es"),
                         "Creo que sí.")

    def test_keeps_real_words(self):
        for text in ("Hummus and umbrellas.", "Hemos llegado.", "The ERM report."):
            self.assertEqual(tp.process(text, language="en"), text)

    def test_portuguese_um_untouched(self):
        self.assertEqual(tp.process("Tenho um carro.", language="pt"), "Tenho um carro.")

    def test_stutter(self):
        self.assertEqual(tp.process("I I think the the plan works."),
                         "I think the plan works.")

    def test_emphatic_no_kept(self):
        self.assertEqual(tp.process("No, no, no."), "No, no, no.")

    def test_disabled(self):
        self.assertEqual(tp.process("Um, hi.", remove_filler_words=False), "Um, hi.")


class Dictionary(unittest.TestCase):
    def test_corrections_and_casing(self):
        out = tp.process("Revisa el repo en github de wisper flow.",
                         words=["GitHub"], corrections={"wisper flow": "Wispr Flow"})
        self.assertEqual(out, "Revisa el repo en GitHub de Wispr Flow.")

    def test_multiword_casing(self):
        self.assertEqual(tp.process("I work at acme cloud.", words=["Acme Cloud"]),
                         "I work at Acme Cloud.")

    def test_no_partial_word_match(self):
        self.assertEqual(tp.process("Proactive people.", words=["Pro"]), "Proactive people.")

    def test_snippet_whole_utterance(self):
        self.assertEqual(tp.process("My email.", snippets={"my email": "me@example.com"}),
                         "me@example.com")

    def test_snippet_mid_sentence(self):
        self.assertEqual(
            tp.process("Send it to my email please.", snippets={"my email": "me@example.com"}),
            "Send it to me@example.com please.")


class Context(unittest.TestCase):
    def test_unknown_context_is_noop(self):
        self.assertEqual(tp.apply_context("Hello there."), "Hello there.")

    def test_adds_space_after_previous_sentence(self):
        self.assertEqual(tp.apply_context("Next one.", before="Done."), " Next one.")

    def test_continues_sentence_lowercase(self):
        self.assertEqual(tp.apply_context("And then we left.", before="We ate dinner "),
                         "and then we left.")

    def test_never_lowercases_proper_nouns(self):
        self.assertEqual(tp.apply_context("Jorge said hi.", before="Yesterday "),
                         "Jorge said hi.")

    def test_capitalises_at_start_of_field(self):
        self.assertEqual(tp.apply_context("hola a todos.", before=""), "Hola a todos.")

    def test_mid_sentence_insert_drops_period(self):
        self.assertEqual(tp.apply_context("really big", before="a ", after="house"),
                         "really big ")
        self.assertEqual(tp.apply_context("Really big.", before="a ", after=" house"),
                         "really big")

    def test_period_kept_before_capitalised_text(self):
        self.assertEqual(tp.apply_context("Done.", before="", after=" Next"), "Done.")

    def test_casual_drops_single_final_period(self):
        self.assertEqual(tp.apply_context("Sounds good.", casual=True), "Sounds good")
        self.assertEqual(tp.apply_context("One. Two.", casual=True), "One. Two.")
        self.assertEqual(tp.apply_context("Really?", casual=True), "Really?")


class VocabPrompt(unittest.TestCase):
    def test_sentences(self):
        from dictation.transcriber import vocab_prompt
        self.assertIsNone(vocab_prompt([], "es"))
        self.assertEqual(vocab_prompt(["Jira"], "en"), "We talked about Jira.")
        self.assertEqual(vocab_prompt(["Acme Cloud", "GitHub", "Jira"], "es"),
                         "Hablamos de Acme Cloud, GitHub y Jira.")
        self.assertEqual(vocab_prompt(["A", "B"], "fr"), "A, B.")


if __name__ == "__main__":
    unittest.main()

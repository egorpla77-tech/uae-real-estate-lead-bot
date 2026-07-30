import unittest

from classifier import classify_lead


class UaeRealEstateClassifierTests(unittest.TestCase):
    def assert_lead(self, text, context="", source_title="Недвижимость Дубая", kind=""):
        signal, reason = classify_lead(text, context=context, source_title=source_title)
        self.assertEqual("matched", reason, msg=f"{text!r}: {reason}")
        self.assertIsNotNone(signal)
        if kind:
            self.assertEqual(kind, signal.kind)
        return signal

    def assert_not_lead(self, text, context="", source_title="Недвижимость Дубая"):
        signal, reason = classify_lead(text, context=context, source_title=source_title)
        self.assertIsNone(signal, msg=f"{text!r}: {signal}; {reason}")

    def test_short_price_uses_post_context(self):
        signal = self.assert_lead("Цена?", "Апартаменты в Business Bay", kind="price")
        self.assertEqual("Дубай", signal.destination)

    def test_handover(self):
        self.assert_lead("Срок сдачи?", "Новая квартира в Дубае", kind="handover")

    def test_layout(self):
        signal = self.assert_lead("Можно планировку квартиры?", kind="layout")
        self.assertEqual("квартира", signal.vehicle)

    def test_mortgage_and_installment(self):
        self.assert_lead("Можно в ипотеку?", "Апартаменты ОАЭ", kind="financing")
        self.assert_lead("Какой первый взнос и есть ли рассрочка?", "Dubai property", kind="financing")

    def test_purchase_request(self):
        signal = self.assert_lead(
            "Хочу купить квартиру с двумя спальнями, бюджет 300 000 USD",
            source_title="Dubai Real Estate",
            kind="purchase",
        )
        self.assertEqual("hot", signal.temperature)

    def test_english_intent(self):
        self.assert_lead("How much? Is mortgage available?", "1BR apartment in Dubai", kind="financing")

    def test_other_emirates(self):
        self.assertEqual(
            "Рас-эль-Хайма",
            self.assert_lead("Какая цена виллы?", "Ras Al Khaimah property", source_title="UAE Real Estate").destination,
        )

    def test_requires_uae_context(self):
        self.assert_not_lead("Цена?", "Квартира в Москве", source_title="Недвижимость России")

    def test_requires_real_estate_context(self):
        self.assert_not_lead("Цена?", "Туры в Дубай", source_title="Отдых в ОАЭ")

    def test_rejects_agents_and_sellers(self):
        self.assert_not_lead("Продаю квартиру в Дубае, пишите в личку")
        self.assert_not_lead("Наше агентство подберёт объект. Пишите в WhatsApp +971 50 000 0000")
        self.assert_not_lead("Dubai Marina 1BR apartment, price 1.8M AED, available now")

    def test_rejects_broker_feed_messages(self):
        broker_messages = [
            (
                "UPDATED PRICE BLUEWATERS RESIDENCES BUILDING 6 3+maid 2,042 sq ft "
                "High floor Full sea view Fully renovated price: 13,100,000 AED "
                "Rented We pay 2% commission Top-up +971506057884"
            ),
            (
                "UPDATE PRICE ADDRESS JBR RESORT&SPA 1 br 855 sqft 2 bathrooms rented "
                "Marina view price: 2,900,000 AED covered top up we pay 2% commissions "
                "+971567354820"
            ),
            (
                "Distress Deal – Address Sky View 5-Bedroom Residence High Floor Full Burj "
                "Khalifa View Approx. 3,100 sq. ft. Fully Furnished Current Market Value: "
                "AED 18M This is a genuine distress opportunity. A viewing is required, "
                "after which we can submit a competitive offer on your behalf."
            ),
            (
                "URGENT BUYER REQUIREMENT We are looking for a vacant residential plot in "
                "Al Khawaneej, Dubai. Buyer: Cash Buyer. Closing: Immediate. Sellers: "
                "Direct Owners & Agents Welcome. Please send asking price and title deed. "
                "Contact: 0555773831"
            ),
        ]
        for message in broker_messages:
            with self.subTest(message=message[:40]):
                self.assert_not_lead(message, source_title="Dubai Real Estate")

    def test_rejects_jobs_and_tours(self):
        self.assert_not_lead("Ищем брокера по недвижимости, работа в Дубае")
        self.assert_not_lead("Сколько стоит горящий тур в Дубай?", source_title="Туры в ОАЭ")


if __name__ == "__main__":
    unittest.main()

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

    def test_user_approved_lead_examples(self):
        approved = [
            (
                "Добрый день всем 🌞 Подскажите, пожалуйста, кто покупал недвижимость в Дубае. "
                "Интересует ипотека для иностранцев. Какие условия, какой нужен первоначальный "
                "взнос и реально ли ее получить?",
                "",
                "Русские в Дубае",
            ),
            (
                "Добрый. Кто может помочь проконсультировать по недвижимости квартиры "
                "ипотека/рассрочка? В лс",
                "",
                "Русские в Дубае",
            ),
            (
                "Hi, could you please send me more details?",
                "1BR apartment for sale in Dubai Marina",
                "Dubai Real Estate",
            ),
            (
                "Looking to buy building in JLT, Commercial-Residential. "
                "Budget: 800 M AED. Only JLT",
                "",
                "Dubai Real Estate",
            ),
        ]
        for text, context, source_title in approved:
            with self.subTest(text=text[:50]):
                self.assert_lead(text, context=context, source_title=source_title)

    def test_rejects_recent_feed_noise(self):
        noise = [
            (
                "All available apartments in Serenia Living. More than 30 active units on the "
                "market. Direct listings from owners only. 2-bedroom Sea View AED 7,500,000. "
                "I can arrange viewings and help find the perfect option for your client. "
                "Working with agents. +971585746968"
            ),
            (
                "FOR SALE – YASMINA, Duet Villa. Expo City Village. Size 3930 sqft. "
                "Handover Q4 2026. Price AED 6,900,000 with post handover plan. "
                "Contact us +971502677214"
            ),
            (
                "I have client looking for one bedroom in District 1 MBR. Ready to move in, "
                "vacant, cash buyer and ready for viewing immediately."
            ),
            (
                "Looking to buy. Serious cash buyer. Client is ready to view and purchase "
                "immediately. Damac Hills townhouse 3BR, budget AED 1.5M. "
                "You should be covered. Please share available options."
            ),
            "Здравствуйте! Кто-то сдает свою квартиру на длительный срок?",
            "Ищу квартиру от собственника в аренду на год",
            "Ищу койко место в Дубае, напишите в личку",
            (
                "Ищу фотографа с собственной фотостудией для создания модельного портфолио. "
                "Отправьте стоимость съемки. Локация: Дубай."
            ),
            (
                "Vacancies are limited for experienced real estate agents in the UAE. "
                "Send me DM for more information and join the company."
            ),
        ]
        for text in noise:
            with self.subTest(text=text[:50]):
                self.assert_not_lead(text, source_title="Dubai Real Estate")


    def test_marks_buyer_from_russia(self):
        signal = self.assert_lead(
            "Я из Москвы, хочу купить квартиру в Дубае. Можно оплатить рублями?",
            kind="purchase",
        )
        self.assertEqual("Россия → ОАЭ", signal.origin)
        self.assertEqual("hot", signal.temperature)

    def test_does_not_infer_russia_from_source_only(self):
        signal = self.assert_lead(
            "Хочу купить квартиру в Дубае, какие есть варианты?",
            source_title="Русские в Дубае",
            kind="purchase",
        )
        self.assertEqual("Недвижимость ОАЭ", signal.origin)


if __name__ == "__main__":
    unittest.main()

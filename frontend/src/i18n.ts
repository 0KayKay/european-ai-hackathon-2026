import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./assistant-ui.en.json";

void i18n.use(initReactI18next).init({
	lng: "en",
	resources: { en: { translation: en } },
	interpolation: { escapeValue: false },
});

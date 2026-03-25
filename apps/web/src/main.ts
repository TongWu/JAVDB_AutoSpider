import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { router } from "./router";
import { i18n, syncLocaleDocumentAndStorage } from "./i18n";
import "./styles.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(i18n);
syncLocaleDocumentAndStorage();
app.mount("#app");

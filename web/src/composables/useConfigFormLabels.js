import { useI18n } from "vue-i18n";
import { humanizeKey, isPathLikeField, sectionSlug, SELECT_OPTION_KEYS } from "../config/configFormConstants";
export { humanizeKey, isPathLikeField, sectionSlug };
export function useConfigFormLabels() {
    const { t, te } = useI18n();
    function sectionTabLabel(section) {
        const k = `config.sections.${sectionSlug(section)}`;
        return te(k) ? t(k) : section.replace(/\s+CONFIGURATION$/i, "").replace(/\s+MODE$/i, "").trim();
    }
    function fieldLabel(key) {
        const k = `config.fields.${key}`;
        return te(k) ? t(k) : humanizeKey(key);
    }
    function fieldDescription(key, meta) {
        const dk = `config.desc.${key}`;
        if (te(dk))
            return t(dk);
        if (meta.readonly)
            return t("config.descGeneric.readonly");
        if (meta.sensitive)
            return t("config.descGeneric.sensitive");
        if (meta.type === "json")
            return t("config.descGeneric.json");
        if (meta.type === "bool")
            return t("config.descGeneric.bool");
        if (meta.type === "int" || meta.type === "float")
            return t("config.descGeneric.number");
        return t("config.descGeneric.default");
    }
    function selectOptionsFor(key, currentRaw) {
        const values = SELECT_OPTION_KEYS[key];
        if (!values?.length)
            return [];
        const cur = String(currentRaw ?? "");
        const has = values.includes(cur);
        const out = values.map((v) => ({
            value: v,
            label: te(`config.select.${key}.${v}`) ? t(`config.select.${key}.${v}`) : v,
        }));
        if (cur && !has) {
            out.push({ value: cur, label: t("config.currentValue", { v: cur }) });
        }
        return out;
    }
    return {
        sectionTabLabel,
        fieldLabel,
        fieldDescription,
        selectOptionsFor,
    };
}

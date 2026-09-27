import { Input } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import styles from "../index.module.less";

interface FilterBarProps {
  isMobile?: boolean;
  filterUserId: string;
  filterTitle: string;
  onUserIdChange: (value: string) => void;
  onTitleChange: (value: string) => void;
}

export function FilterBar({
  isMobile,
  filterUserId,
  filterTitle,
  onUserIdChange,
  onTitleChange,
}: FilterBarProps) {
  const { t } = useTranslation();

  return (
    <div className={styles.filterBar}>
      <Input
        placeholder={t("sessions.filterTitle")}
        value={filterTitle}
        onChange={(e) => onTitleChange(e.target.value)}
        allowClear
        className="sessions-filter-input"
        style={isMobile ? { width: "100%" } : { width: 200, marginRight: 8 }}
      />
      <Input
        placeholder={t("sessions.filterUserId")}
        value={filterUserId}
        onChange={(e) => onUserIdChange(e.target.value)}
        allowClear
        className="sessions-filter-input"
        style={isMobile ? { width: "100%" } : { width: 200, marginRight: 8 }}
      />
    </div>
  );
}

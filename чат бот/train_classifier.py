import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import AutoTokenizer, AutoModel
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix
from tqdm import tqdm
import pickle
import time


MODEL_NAME = "cointegrated/rubert-tiny2"
MAX_LEN = 64
EPOCHS = 35
LR = 2e-5
BATCH_SIZE = 32


print("ОБУЧЕНИЕ КЛАССИФИКАТОРА НАМЕРЕНИЙ")

print("\n[1/6] Загрузка датасета...")
df = pd.read_csv("intents.csv", encoding="cp1251", engine='python', on_bad_lines='skip')
print(f"  Всего примеров: {len(df)}")
print(f"  Уникальных категорий: {df['category'].nunique()}")
print("\n  Распределение по категориям:")
for cat, count in df['category'].value_counts().items():
    bar = "█" * min(count, 40)
    print(f"    {cat:30} {count:4} {bar}")

le = LabelEncoder()
df['label'] = le.fit_transform(df['category'])
num_classes = len(le.classes_)
print(f"\n  Классов: {num_classes}")

with open("label_encoder.pkl", "wb") as f:
    pickle.dump(le, f)

print("\n[2/6] Разделение на train/val (80/20)...")

from collections import Counter
label_counts = Counter(df['label'])
rare_labels = [label for label, count in label_counts.items() if count < 2]

if rare_labels:
    print(f"  ⚠️  Категорий с 1 примером: {len(rare_labels)}")
    print(f"  Эти категории будут только в train (без стратификации)")
    for label in rare_labels:
        cat = le.inverse_transform([label])[0]
        print(f"    - {cat}")
    
    train_df = df[~df['label'].isin(rare_labels)]
    rare_df = df[df['label'].isin(rare_labels)]
    
    train_df, val_df = train_test_split(
        train_df, test_size=0.2, random_state=42, stratify=train_df['label']
    )
    train_df = pd.concat([train_df, rare_df], ignore_index=True)
else:
    train_df, val_df = train_test_split(
        df, test_size=0.2, random_state=42, stratify=df['label']
    )

print(f"  Train: {len(train_df)} примеров")
print(f"  Val:   {len(val_df)} примеров")
print(f"  Train: {len(train_df)} примеров")
print(f"  Val:   {len(val_df)} примеров")

print("\n[3/6] Загрузка токенизатора...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
print(f"  Токенизатор: {MODEL_NAME}")


class IntentDataset(Dataset):
    def __init__(self, texts, labels):
        self.texts = texts
        self.labels = labels

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        enc = tokenizer(
            self.texts[idx],
            truncation=True,
            padding='max_length',
            max_length=MAX_LEN,
            return_tensors='pt'
        )
        return {
            'input_ids': enc['input_ids'].squeeze(),
            'attention_mask': enc['attention_mask'].squeeze(),
            'label': torch.tensor(self.labels[idx], dtype=torch.long)
        }


train_ds = IntentDataset(train_df['text'].tolist(), train_df['label'].tolist())
val_ds = IntentDataset(val_df['text'].tolist(), val_df['label'].tolist())

train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_ds, batch_size=32)


class IntentClassifier(nn.Module):
    def __init__(self, num_classes, model_name=MODEL_NAME):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        self.classifier = nn.Sequential(
            nn.Dropout(0.3),
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, num_classes)
        )

    def forward(self, input_ids, attention_mask):
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        cls_embedding = outputs.last_hidden_state[:, 0, :]
        logits = self.classifier(cls_embedding)
        return logits


print("\n[4/6] Инициализация модели...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"  Устройство: {device}")

model = IntentClassifier(num_classes=num_classes).to(device)
total_params = sum(p.numel() for p in model.parameters())
trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"  Всего параметров: {total_params:,}")
print(f"  Обучаемых: {trainable_params:,}")

optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)
criterion = nn.CrossEntropyLoss()

print("\n[5/6] Обучение...")
print("=" * 70)
print(f"{'Эпоха':<8} {'Train Loss':<12} {'Val Loss':<12} {'Val Acc':<10} {'Время':<10} {'Статус'}")
print("-" * 70)

best_val_acc = 0.0
history = []

for epoch in range(EPOCHS):
    epoch_start = time.time()

    model.train()
    train_loss = 0.0
    for batch in train_loader:
        input_ids = batch['input_ids'].to(device)
        attention_mask = batch['attention_mask'].to(device)
        labels = batch['label'].to(device)

        optimizer.zero_grad()
        logits = model(input_ids, attention_mask)
        loss = criterion(logits, labels)
        loss.backward()
        optimizer.step()
        train_loss += loss.item()

    avg_train_loss = train_loss / len(train_loader)

    model.eval()
    val_loss = 0.0
    correct = 0
    total = 0
    all_preds = []
    all_labels = []

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['label'].to(device)

            logits = model(input_ids, attention_mask)
            loss = criterion(logits, labels)
            val_loss += loss.item()

            preds = logits.argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)

            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    avg_val_loss = val_loss / len(val_loader)
    val_acc = correct / total
    epoch_time = time.time() - epoch_start

    status = ""
    if val_acc > best_val_acc:
        best_val_acc = val_acc
        torch.save({
            'model_state_dict': model.state_dict(),
            'num_classes': num_classes,
            'model_name': MODEL_NAME,
        }, "intent_classifier.pt")
        status = "✓ сохранено"

    history.append({
        'epoch': epoch + 1,
        'train_loss': avg_train_loss,
        'val_loss': avg_val_loss,
        'val_acc': val_acc,
    })

    print(f"{epoch+1:<8} {avg_train_loss:<12.4f} {avg_val_loss:<12.4f} {val_acc:<10.4f} {epoch_time:<10.1f}с {status}")


print(f"\n[6/6] Итоги обучения:")
print(f"  Лучшая точность на валидации: {best_val_acc:.4f}")
print(f"  Модель сохранена: intent_classifier.pt")

print("\n  Метрики по классам на валидации:")
unique_labels = sorted(set(all_labels) | set(all_preds))
target_names = [le.classes_[i] for i in unique_labels]

print(classification_report(
    all_labels, all_preds,
    labels=unique_labels,
    target_names=target_names,
    zero_division=0
))

print("\n  Матрица ошибок:")
cm = confusion_matrix(all_labels, all_preds, labels=unique_labels)
print(f"{'':30}", end="")
for name in target_names:
    print(f"{name[:6]:>8}", end="")
print()
for i, name in enumerate(target_names):
    print(f"{name:30}", end="")
    for j in range(len(target_names)):
        val = cm[i][j]
        if i == j:
            print(f"\033[92m{val:>8}\033[0m", end="")
        elif val > 0:
            print(f"\033[91m{val:>8}\033[0m", end="")
        else:
            print(f"{val:>8}", end="")
    print()

print("\n" + "=" * 70)
print("ГОТОВО! Модель обучена.")
print("=" * 70)

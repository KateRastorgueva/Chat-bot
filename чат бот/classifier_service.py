import torch
import torch.nn as nn
import pickle
import yaml
from transformers import AutoTokenizer, AutoModel


class IntentRouter:
    def __init__(self, model_path="intent_classifier.pt",
                 tokenizer_name="cointegrated/rubert-tiny2",
                 urls_path="urls.yaml",
                 device="cpu"):
        self.device = torch.device(device)
        self.max_len = 64

        checkpoint = torch.load(model_path, map_location=self.device)
        self.num_classes = checkpoint['num_classes']
        self.model_name = checkpoint['model_name']

        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
        self.model = self._build_model()
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

        with open("label_encoder.pkl", "rb") as f:
            self.le = pickle.load(f)

        with open(urls_path, "r", encoding="utf-8") as f:
            self.urls = yaml.safe_load(f)

    def _build_model(self):
        class IntentClassifier(nn.Module):
            def __init__(self, num_classes, model_name):
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
                cls = outputs.last_hidden_state[:, 0, :]
                return self.classifier(cls)

        return IntentClassifier(self.num_classes, self.model_name).to(self.device)

    def route(self, question: str, threshold: float = 0.6):
        enc = self.tokenizer(
            question, truncation=True, padding='max_length',
            max_length=self.max_len, return_tensors='pt'
        )
        with torch.no_grad():
            logits = self.model(
                enc['input_ids'].to(self.device),
                enc['attention_mask'].to(self.device)
            )
            probs = torch.softmax(logits, dim=1)[0]
            conf, idx = probs.max(dim=0)

        conf = conf.item()
        if conf < threshold:
            return {"category": None, "confidence": conf, "url": None, "urls": []}

        category = self.le.inverse_transform([idx.item()])[0]

        url_data = self.urls.get(category, {})
        if isinstance(url_data, dict):
            main_url = url_data.get("url")
            fallback = url_data.get("fallback_urls", [])
            all_urls = [main_url] + fallback if main_url else fallback
        else:
            main_url = url_data
            all_urls = [url_data] if url_data else []
        
        seen = set()
        urls = []
        for u in all_urls:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        return {
            "category": category,
            "confidence": conf,
            "url": main_url,
            "urls": urls,
        }


if __name__ == "__main__":
    router = IntentRouter(device="cpu")

    test_questions = [
        "Как получить справку о стипендии?",
        "Где находится общага №3?",
        "Что такое ГПО?",
        "Когда начинается практика?",
        "Как поступить в магистратуру?",
        "Какая погода в Томске?",
    ]

    for q in test_questions:
        result = router.route(q)
        print(f"{q!r:55} → {result['category']} (conf={result['confidence']:.3f}) | {result['url']}")

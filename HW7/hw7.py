# -*- coding: utf-8 -*-
"""Copy_of_hw7_bert__try_ipynb.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/18cJGmeNhF2MpC4JMPOjQd27SOJtZB7l8

# **Homework 7 - Bert (Question Answering)**

If you have any questions, feel free to email us at ntu-ml-2021spring-ta@googlegroups.com



Slide:    [Link](https://docs.google.com/presentation/d/1aQoWogAQo_xVJvMQMrGaYiWzuyfO0QyLLAhiMwFyS2w)　Kaggle: [Link](https://www.kaggle.com/c/ml2021-spring-hw7)　Data: [Link](https://drive.google.com/uc?id=1znKmX08v9Fygp-dgwo7BKiLIf2qL1FH1)

## Task description
- Chinese Extractive Question Answering
  - Input: Paragraph + Question
  - Output: Answer

- Objective: Learn how to fine tune a pretrained model on downstream task using transformers

- Todo
    - Fine tune a pretrained chinese BERT model
    - Change hyperparameters (e.g. doc_stride)
    - Apply linear learning rate decay
    - Try other pretrained models
    - Improve preprocessing
    - Improve postprocessing
- Training tips
    - Automatic mixed precision
    - Gradient accumulation
    - Ensemble

- Estimated training time (tesla t4 with automatic mixed precision enabled)
    - Simple: 8mins
    - Medium: 8mins
    - Strong: 25mins
    - Boss: 2hrs

## Download Dataset
"""

!nvidia-smi

# Download link 1
!gdown --id '1znKmX08v9Fygp-dgwo7BKiLIf2qL1FH1' --output hw7_data.zip

# Download Link 2 (if the above link fails) 
# !gdown --id '1pOu3FdPdvzielUZyggeD7KDnVy9iW1uC' --output hw7_data.zip

!unzip -o hw7_data.zip

# For this HW, K80 < P4 < T4 < P100 <= T4(fp16) < V100
!nvidia-smi

"""## Install transformers

Documentation for the toolkit:　https://huggingface.co/transformers/
"""

# You are allowed to change version of transformers or use other toolkits
!pip install transformers==4.5.0

"""## Import Packages"""

import json
import numpy as np
import random
import torch
from torch.utils.data import DataLoader, Dataset 
from transformers import AdamW, BertForQuestionAnswering, BertTokenizerFast

from tqdm.auto import tqdm

device = "cuda" if torch.cuda.is_available() else "cpu"

# Fix random seed for reproducibility
def same_seeds(seed):
	  torch.manual_seed(seed)
	  if torch.cuda.is_available():
		    torch.cuda.manual_seed(seed)
		    torch.cuda.manual_seed_all(seed)
	  np.random.seed(seed)
	  random.seed(seed)
	  torch.backends.cudnn.benchmark = False
	  torch.backends.cudnn.deterministic = True
same_seeds(0)

# Change "fp16_training" to True to support automatic mixed precision training (fp16)	
fp16_training = True

if fp16_training:
    !pip install accelerate==0.2.0
    from accelerate import Accelerator
    accelerator = Accelerator(fp16=True)
    device = accelerator.device

# Documentation for the toolkit:  https://huggingface.co/docs/accelerate/

"""## Load Model and Tokenizer




 
"""

model = BertForQuestionAnswering.from_pretrained("hfl/chinese-roberta-wwm-ext-large").to(device)
tokenizer = BertTokenizerFast.from_pretrained("hfl/chinese-roberta-wwm-ext-large")

# You can safely ignore the warning message (it pops up because new prediction heads for QA are initialized randomly)

"""## Read Data

- Training set: 26935 QA pairs
- Dev set: 3523  QA pairs
- Test set: 3492  QA pairs

- {train/dev/test}_questions:	
  - List of dicts with the following keys:
   - id (int)
   - paragraph_id (int)
   - question_text (string)
   - answer_text (string)
   - answer_start (int)
   - answer_end (int)
- {train/dev/test}_paragraphs: 
  - List of strings
  - paragraph_ids in questions correspond to indexs in paragraphs
  - A paragraph may be used by several questions 
"""

def read_data(file):
    with open(file, 'r', encoding="utf-8") as reader:
        data = json.load(reader)
    return data["questions"], data["paragraphs"]

train_questions, train_paragraphs = read_data("hw7_train.json")
dev_questions, dev_paragraphs = read_data("hw7_dev.json")
test_questions, test_paragraphs = read_data("hw7_test.json")

"""## Tokenize Data"""

# Tokenize questions and paragraphs separately
# 「add_special_tokens」 is set to False since special tokens will be added when tokenized questions and paragraphs are combined in datset __getitem__ 

train_questions_tokenized = tokenizer([train_question["question_text"] for train_question in train_questions], add_special_tokens=False)
dev_questions_tokenized = tokenizer([dev_question["question_text"] for dev_question in dev_questions], add_special_tokens=False)
test_questions_tokenized = tokenizer([test_question["question_text"] for test_question in test_questions], add_special_tokens=False) 

train_paragraphs_tokenized = tokenizer(train_paragraphs, add_special_tokens=False)
dev_paragraphs_tokenized = tokenizer(dev_paragraphs, add_special_tokens=False)
test_paragraphs_tokenized = tokenizer(test_paragraphs, add_special_tokens=False)

# You can safely ignore the warning message as tokenized sequences will be futher processed in datset __getitem__ before passing to model

"""## Dataset and Dataloader"""

import random

class QA_Dataset(Dataset):
    def __init__(self, split, questions, tokenized_questions, tokenized_paragraphs):
        self.split = split
        self.questions = questions
        self.tokenized_questions = tokenized_questions
        self.tokenized_paragraphs = tokenized_paragraphs
        self.max_question_len = 40
        self.max_paragraph_len = 180
        
        ##### TODO: Change value of doc_stride #####
        self.doc_stride = 60

        # Input sequence length = [CLS] + question + [SEP] + paragraph + [SEP]
        self.max_seq_len = 1 + self.max_question_len + 1 + self.max_paragraph_len + 1

    def __len__(self):
        return len(self.questions)

    def __getitem__(self, idx):
        question = self.questions[idx]
        tokenized_question = self.tokenized_questions[idx]
        tokenized_paragraph = self.tokenized_paragraphs[question["paragraph_id"]]

        ##### TODO: Preprocessing #####
        # Hint: How to prevent model from learning something it should not learn

        if self.split == "train":
            # Convert answer's start/end positions in paragraph_text to start/end positions in tokenized_paragraph  
            answer_start_token = tokenized_paragraph.char_to_token(question["answer_start"])
            answer_end_token = tokenized_paragraph.char_to_token(question["answer_end"])

            # A single window is obtained by slicing the portion of paragraph containing the answer
            # mid = (answer_start_token + answer_end_token) // 2

            # rate = random.random()
            # between = (answer_start_token * (1-rate)) + (answer_end_token * rate)
            # between = int(between)
            # half_paragraph = self.max_paragraph_len // 2
            # paragraph_start = max (0, between - half_paragraph)
            # paragraph_end = paragraph_start + self.max_paragraph_len

            answer_len = answer_end_token - answer_start_token + 1
            shift = random.randint(0, self.max_paragraph_len )# - answer_len)

            paragraph_start = max(0, min(answer_start_token - shift, len(tokenized_paragraph) - self.max_paragraph_len))

            # paragraph_start = max(0, answer_start_token - shift)
            paragraph_end = paragraph_start + self.max_paragraph_len

            # paragraph_start = max(0, min(between - self.max_paragraph_len // 2, len(tokenized_paragraph) - self.max_paragraph_len))
            ### paragraph_start = max(0, answer_start_token - random.randint(0, self.max_paragraph_len))
            ### paragraph_end = paragraph_start + self.max_paragraph_len


            
            # Slice question/paragraph and add special tokens (101: CLS, 102: SEP)
            input_ids_question = [101] + tokenized_question.ids[:self.max_question_len] + [102] 
            input_ids_paragraph = tokenized_paragraph.ids[paragraph_start : paragraph_end] + [102]		
            
            # Convert answer's start/end positions in tokenized_paragraph to start/end positions in the window  
            answer_start_token += len(input_ids_question) - paragraph_start
            answer_end_token += len(input_ids_question) - paragraph_start
            
            # Pad sequence and obtain inputs to model 
            input_ids, token_type_ids, attention_mask = self.padding(input_ids_question, input_ids_paragraph)
            return torch.tensor(input_ids), torch.tensor(token_type_ids), torch.tensor(attention_mask), answer_start_token, answer_end_token

        # Validation/Testing
        else:
            input_ids_list, token_type_ids_list, attention_mask_list = [], [], []
            
            # Paragraph is split into several windows, each with start positions separated by step "doc_stride"
            for i in range(0, len(tokenized_paragraph), self.doc_stride):
                
                # Slice question/paragraph and add special tokens (101: CLS, 102: SEP)
                input_ids_question = [101] + tokenized_question.ids[:self.max_question_len] + [102]
                input_ids_paragraph = tokenized_paragraph.ids[i : i + self.max_paragraph_len] + [102]
                
                # Pad sequence and obtain inputs to model
                input_ids, token_type_ids, attention_mask = self.padding(input_ids_question, input_ids_paragraph)
                
                input_ids_list.append(input_ids)
                token_type_ids_list.append(token_type_ids)
                attention_mask_list.append(attention_mask)
            
            return torch.tensor(input_ids_list), torch.tensor(token_type_ids_list), torch.tensor(attention_mask_list)

    def padding(self, input_ids_question, input_ids_paragraph):
        # Pad zeros if sequence length is shorter than max_seq_len
        padding_len = self.max_seq_len - len(input_ids_question) - len(input_ids_paragraph)
        # Indices of input sequence tokens in the vocabulary
        input_ids = input_ids_question + input_ids_paragraph + [0] * padding_len
        # Segment token indices to indicate first and second portions of the inputs. Indices are selected in [0, 1]
        token_type_ids = [0] * len(input_ids_question) + [1] * len(input_ids_paragraph) + [0] * padding_len
        # Mask to avoid performing attention on padding token indices. Mask values selected in [0, 1]
        attention_mask = [1] * (len(input_ids_question) + len(input_ids_paragraph)) + [0] * padding_len
        
        return input_ids, token_type_ids, attention_mask

train_set = QA_Dataset("train", train_questions, train_questions_tokenized, train_paragraphs_tokenized)
dev_set = QA_Dataset("dev", dev_questions, dev_questions_tokenized, dev_paragraphs_tokenized)
test_set = QA_Dataset("test", test_questions, test_questions_tokenized, test_paragraphs_tokenized)

train_batch_size = 16

# Note: Do NOT change batch size of dev_loader / test_loader !
# Although batch size=1, it is actually a batch consisting of several windows from the same QA pair
train_loader = DataLoader(train_set, batch_size=train_batch_size, shuffle=True, pin_memory=True)
dev_loader = DataLoader(dev_set, batch_size=1, shuffle=False, pin_memory=True)
test_loader = DataLoader(test_set, batch_size=1, shuffle=False, pin_memory=True)

"""## Function for Evaluation"""

def evaluate(data, output):
    ##### TODO: Postprocessing #####
    # There is a bug and room for improvement in postprocessing 
    # Hint: Open your prediction file to see what is wrong 
    
    answer = ''
    max_prob = float('-inf')
    num_of_windows = data[0].shape[1]
    
    for k in range(num_of_windows):
        # Obtain answer by choosing the most probable start position / end position
        start_prob, start_index = torch.max(output.start_logits[k], dim=0)
        end_prob, end_index = torch.max(output.end_logits[k], dim=0)
        
        # Probability of answer is calculated as sum of start_prob and end_prob
        prob = start_prob + end_prob
        
        if end_index < start_index:
          prob = 0.

        # while end_index < start_index:
        #   if start_prob > end_prob:
        #     output.end_logits[k][end_index] = 0
        #     end_prob, end_index = torch.max(output.end_logits[k], dim=0)
        #     prob = start_prob + end_prob
        #   else:
        #     output.start_logits[k][start_index] = 0
        #     start_prob, start_index = torch.max(output.start_logits[k], dim=0)
        #     prob = start_prob + end_prob

        # if end_index < start_index:
        #   temp = end_index
        #   end_index = start_index
        #   start_index = temp
        
        # Replace answer if calculated probability is larger than previous windows
        if prob > max_prob:
            max_prob = prob
            # Convert tokens to chars (e.g. [1920, 7032] --> "大 金")
            answer = tokenizer.decode(data[0][0][k][start_index : end_index + 1])
    
    left_side = 0
    right_side = 0
    for word in answer:
      if word == '《':
        left_side = 1
      if word == '》':
        right_side = 1
      if word == '「':
        answer = answer[1:len(answer)]
      if word == '」':
        answer = answer[0:len(answer)-1]

    if left_side == 0 and right_side == 1:
      answer = '《' + answer
    if left_side == 1 and right_side == 0:
      answer = answer + '》'
      
    # Remove spaces in answer (e.g. "大 金" --> "大金")
    return answer.replace(' ','')

"""## Training"""

# import matplotlib.pyplot as plt
# import numpy as np
# lr_factor=0.005
# lr_warmup=40
# record = [0]*1686
# for step in range(1, 1685):
#   temp = lr_factor * (16 ** (-0.5) * min(step ** (-0.5), step * lr_warmup ** (-1.5))) # 16 self.model_size
#   # optimizer.param_groups[0]["lr"] = 1e-4 - (1e-4 * step) / 1685.0
#   record[step] = temp

# plt.plot(np.arange(1, 1685), [record[i] for i in range(1, 1685)])
# # None

import matplotlib.pyplot as plt

num_epoch = 1 
validation = True
logging_step = 100
learning_rate = 0
record = [0]*1686
record[1] = learning_rate
# best_acc = 0
optimizer = AdamW(model.parameters(), lr=learning_rate)

if fp16_training:
    model, optimizer, train_loader = accelerator.prepare(model, optimizer, train_loader) 

model.train()

print("Start Training ...")

for epoch in range(num_epoch):
    step = 1
    train_loss = train_acc = 0
    
    for data in tqdm(train_loader):	
        # Load all data into GPU
        data = [i.to(device) for i in data]
        # Model inputs: input_ids, token_type_ids, attention_mask, start_positions, end_positions (Note: only "input_ids" is mandatory)
        # Model outputs: start_logits, end_logits, loss (return when start_positions/end_positions are provided)  
        output = model(input_ids=data[0], token_type_ids=data[1], attention_mask=data[2], start_positions=data[3], end_positions=data[4])

        # Choose the most probable start position / end position
        start_index = torch.argmax(output.start_logits, dim=1)
        end_index = torch.argmax(output.end_logits, dim=1)
        
        # Prediction is correct only if both start_index and end_index are correct
        train_acc += ((start_index == data[3]) & (end_index == data[4])).float().mean()
        train_loss += output.loss
        
        if fp16_training:
            accelerator.backward(output.loss)
        else:
            output.loss.backward()

        optimizer.step()
        optimizer.zero_grad()
        learning_rate = record[step]
        step += 1

        ##### TODO: Apply linear learning rate decay #####
        total_step = 1685

        lr_factor=0.005
        lr_warmup=40

        # optimizer.param_groups[0]["lr"] = lr_factor * (16 ** (-0.5) * min(step ** (-0.5), step * lr_warmup ** (-1.5))) # 16 self.model_size
        # optimizer.param_groups[0]["lr"] = 1e-4 - (1e-4 * step) / 1685.0
        if step < 170:
          optimizer.param_groups[0]["lr"] = 1e-4 * (step / 169.)
        else:
          optimizer.param_groups[0]["lr"] = 1e-4 - 1e-4 * ((step - 170) / 1515.)

        record[step] = optimizer.param_groups[0]["lr"]
        
        # Print training loss and accuracy over past logging step
        if step % logging_step == 0:
            # if train_acc > best_acc:
            #   best_acc = train_acc
            #   print("Saving Model ...")
            #   model_save_dir = "saved_model" 
            #   model.save_pretrained(model_save_dir)
            print(f"Epoch {epoch + 1} | Step {step} | loss = {train_loss.item() / logging_step:.3f}, acc = {train_acc / logging_step:.3f}")
            train_loss = train_acc = 0

    if validation:
        print("Evaluating Dev Set ...")
        model.eval()
        with torch.no_grad():
            dev_acc = 0
            for i, data in enumerate(tqdm(dev_loader)):
                output = model(input_ids=data[0].squeeze(dim=0).to(device), token_type_ids=data[1].squeeze(dim=0).to(device),
                       attention_mask=data[2].squeeze(dim=0).to(device))
                # prediction is correct only if answer text exactly matches
                dev_acc += evaluate(data, output) == dev_questions[i]["answer_text"]
            print(f"Validation | Epoch {epoch + 1} | acc = {dev_acc / len(dev_loader):.3f}")
        model.train()

plt.plot(np.arange(1, 1685), [record[i] for i in range(1, 1685)])
None
# Save a model and its configuration file to the directory 「saved_model」 
# i.e. there are two files under the direcory 「saved_model」: 「pytorch_model.bin」 and 「config.json」
# Saved model can be re-loaded using 「model = BertForQuestionAnswering.from_pretrained("saved_model")」

print("Saving Model ...")
model_save_dir = "saved_model" 
model.save_pretrained(model_save_dir)

"""## Testing"""

print("Evaluating Test Set ...")

result = []

model.eval()
with torch.no_grad():
    for data in tqdm(test_loader):
        output = model(input_ids=data[0].squeeze(dim=0).to(device), token_type_ids=data[1].squeeze(dim=0).to(device),
                       attention_mask=data[2].squeeze(dim=0).to(device))
        result.append(evaluate(data, output))

result_file = "result.csv"
with open(result_file, 'w') as f:	
	  f.write("ID,Answer\n")
	  for i, test_question in enumerate(test_questions):
        # Replace commas in answers with empty strings (since csv is separated by comma)
        # Answers in kaggle are processed in the same way
		    f.write(f"{test_question['id']},{result[i].replace(',','')}\n")

print(f"Completed! Result is in {result_file}")
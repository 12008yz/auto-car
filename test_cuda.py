import torch

print("Python / torch:", torch.__version__)
print("CUDA в сборке torch:", torch.version.cuda)
print("CUDA доступна:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("Видеокарта:", torch.cuda.get_device_name(0))
    print("Память GPU, ГБ:", round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2))
    x = torch.randn(1024, 1024, device="cuda")
    y = x @ x
    print("Тестовое умножение на GPU: OK, сумма =", float(y.sum()))
else:
    print("GPU недоступна — проверьте драйвер NVIDIA и что torch установлен с CUDA.")

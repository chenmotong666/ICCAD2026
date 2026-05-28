module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n22,
    n20,
    n21,
    n31,
    n32,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n22;
  output n20;
  output n21;
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test56/test56.v:62$20_Y , \\\$and$testcase/test56/test56.v:10$4_Y , \\\$auto$rtlil.cc:3255:Not$38 , \\\$or$testcase/test56/test56.v:11$6_Y , \\\$xor$testcase/test56/test56.v:12$8_Y , ___or_testcase_test56_test56_v_24_15_not_b_0__, ___or_testcase_test56_test56_v_53_14_not_a_0__, ___or_testcase_test56_test56_v_53_14_not_b_0__, ___or_testcase_test56_test56_v_54_15_not_a_0__, ___or_testcase_test56_test56_v_54_15_not_b_0__, ___or_testcase_test56_test56_v_56_16_not_a_0__, ___or_testcase_test56_test56_v_56_16_not_b_0__, ___or_testcase_test56_test56_v_59_18_not_a_0__, ___or_testcase_test56_test56_v_59_18_not_b_0__, ___or_testcase_test56_test56_v_69_25_not_a_0__, ___or_testcase_test56_test56_v_69_25_not_b_0__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test56/test56.v:37$1  (n107, n2, n3);
  and   \$and$testcase/test56/test56.v:38$2  (n109, n2, n3);
  and   \$and$testcase/test56/test56.v:39$3  (n110, n2, n3);
  and   \$and$testcase/test56/test56.v:40$4  (n120, n2, n3);
  and   \$and$testcase/test56/test56.v:41$5  (n126, n2, n3);
  and   \$and$testcase/test56/test56.v:42$6  (n100, n2, n3);
  and   \$and$testcase/test56/test56.v:43$7  (n121, n2, n4);
  and   \$and$testcase/test56/test56.v:44$8  (n127, n2, n4);
  not   \$not$testcase/test56/test56.v:45$27  (n111, n4);
  not   \$not$testcase/test56/test56.v:46$28  (n114, n4);
  not   or_not_3 (___or_testcase_test56_test56_v_54_15_not_b_0__, n4);
  and   \$and$testcase/test56/test56.v:47$9  (n122, n2, n5);
  not   \$not$testcase/test56/test56.v:48$29  (\\\$auto$rtlil.cc:3255:Not$38 , n5);
  not   or_not_1 (___or_testcase_test56_test56_v_53_14_not_b_0__, n5);
  and   \$and$testcase/test56/test56.v:49$10  (n123, n2, n6);
  and   \$and$testcase/test56/test56.v:50$11  (n124, n2, n7);
  and   \$and$testcase/test56/test56.v:51$12  (n140, n6, n7);
  not   or_not_9 (___or_testcase_test56_test56_v_69_25_not_b_0__, n7);
  and   \$and$testcase/test56/test56.v:52$13  (n125, n2, n8);
  not   or_not_7 (___or_testcase_test56_test56_v_59_18_not_b_0__, n8);
  not   or_not_0 (___or_testcase_test56_test56_v_53_14_not_a_0__, n107);
  not   or_not_4 (___or_testcase_test56_test56_v_56_16_not_a_0__, n120);
  not   \$not$testcase/test56/test56.v:55$30  (___or_testcase_test56_test56_v_24_15_not_b_0__, n100);
  not   or_not_2 (___or_testcase_test56_test56_v_54_15_not_a_0__, n100);
  not   or_not_5 (___or_testcase_test56_test56_v_56_16_not_b_0__, n121);
  xor   \$xor$testcase/test56/test56.v:58$17  (n129, n122, n123);
  not   or_not_6 (___or_testcase_test56_test56_v_59_18_not_a_0__, n140);
  nand  \$or$testcase/test56/test56.v:53$14  (n108, ___or_testcase_test56_test56_v_53_14_not_a_0__, ___or_testcase_test56_test56_v_53_14_not_b_0__);
  and   \$and$testcase/test56/test56.v:62$20  (\$and$testcase/test56/test56.v:62$20_Y , n4, ___or_testcase_test56_test56_v_24_15_not_b_0__);
  nand  \$or$testcase/test56/test56.v:54$15  (n101, ___or_testcase_test56_test56_v_54_15_not_a_0__, ___or_testcase_test56_test56_v_54_15_not_b_0__);
  nand  \$or$testcase/test56/test56.v:56$16  (n128, ___or_testcase_test56_test56_v_56_16_not_a_0__, ___or_testcase_test56_test56_v_56_16_not_b_0__);
  nand  \$or$testcase/test56/test56.v:59$18  (n141, ___or_testcase_test56_test56_v_59_18_not_a_0__, ___or_testcase_test56_test56_v_59_18_not_b_0__);
  xor   \$xor$testcase/test56/test56.v:60$19  (n21, n108, n6);
  not   \$not$testcase/test56/test56.v:62$21  (n22, \$and$testcase/test56/test56.v:62$20_Y );
  not   \$not$testcase/test56/test56.v:61$32  (n102, n101);
  and   \$and$testcase/test56/test56.v:63$22  (n130, n129, n128);
  not   \$not$testcase/test56/test56.v:64$33  (n142, n141);
  xor   \$xor$testcase/test56/test56.v:65$23  (n103, n102, n5);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  and   \$and$testcase/test56/test56.v:67$24  (\\\$and$testcase/test56/test56.v:10$4_Y , n103, n6);
  not   \$not$testcase/test56/test56.v:68$34  (n104, \\\$and$testcase/test56/test56.v:10$4_Y );
  not   or_not_8 (___or_testcase_test56_test56_v_69_25_not_a_0__, n104);
  nand  \$or$testcase/test56/test56.v:69$25  (\\\$or$testcase/test56/test56.v:11$6_Y , ___or_testcase_test56_test56_v_69_25_not_a_0__, ___or_testcase_test56_test56_v_69_25_not_b_0__);
  not   \$not$testcase/test56/test56.v:70$35  (n105, \\\$or$testcase/test56/test56.v:11$6_Y );
  xor   \$xor$testcase/test56/test56.v:71$26  (\\\$xor$testcase/test56/test56.v:12$8_Y , n105, n8);
  not   \$not$testcase/test56/test56.v:72$36  (n20, \\\$xor$testcase/test56/test56.v:12$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = n5;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
